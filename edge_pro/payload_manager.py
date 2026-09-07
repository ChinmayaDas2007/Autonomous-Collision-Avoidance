"""
Project Kessler - Edge Processing Payload
Mission Management & Slew Targeting Orchestrator (Phase 1 Entrypoint)
-------------------------------------------------------------------------------
Coordinates the non-blocking TCP Ingestion Server, Observation Scheduler,
Payload State Machine (FSM), TCP Slew Command Publisher to core_phy,
and the Phase 2 Vision Pipeline interface.

Usage:
  python3 -m edge_pro.payload_manager
  python3 -m edge_pro.payload_manager --immediate --ingest-port 5555 --core-phy-port 5556
"""

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import signal
import sys
from typing import Optional

from edge_pro.config import PayloadConfig
from edge_pro.models import RefinedCDM, SlewCommand, PacketValidationError
from edge_pro.scheduler import ObservationScheduler, ObservationSchedule, format_iso8601_utc
from edge_pro.state_machine import PayloadStateMachine, PayloadState, StateTransitionError
from edge_pro.tcp_server import TCPIngestServer
from edge_pro.tcp_client import TCPSlewPublisher
from edge_pro.vision_interface import VisionPipelineInterface, MockVisionPipeline

logger = logging.getLogger("edge_pro.manager")


class PayloadManager:
    """
    Master coordinator for Phase 1 of the Project Kessler Edge Payload.
    Integrates ingestion, mission scheduling, ADCS command dispatch,
    and payload state transitions.
    """

    def __init__(
        self,
        config: Optional[PayloadConfig] = None,
        vision_pipeline: Optional[VisionPipelineInterface] = None,
    ):
        self.config = config or PayloadConfig()
        self._configure_logging()

        logger.info("Initializing Project Kessler Edge Processing Payload (Phase 1)...")

        # 1. Finite State Machine
        self.fsm = PayloadStateMachine(initial_state=PayloadState.BOOT)

        # 2. Vision Pipeline Hook (Phase 2 modular slot)
        self.vision = vision_pipeline or MockVisionPipeline()

        # 3. Observation Scheduler (Execution only; Ground AI owns scheduling math)
        self.scheduler = ObservationScheduler(
            immediate_dispatch=self.config.immediate_dispatch,
            time_scale_factor=self.config.time_scale_factor,
        )

        # 4. TCP Networking Nodes
        self.ingest_server = TCPIngestServer(
            host=self.config.ingest_host,
            port=self.config.ingest_port,
            on_cdm_callback=self.handle_incoming_cdm,
            max_buffer_size=self.config.max_packet_bytes,
        )

        self.slew_publisher = TCPSlewPublisher(
            host=self.config.core_phy_host,
            port=self.config.core_phy_port,
            reconnect_interval=self.config.reconnect_interval_seconds,
        )

        # Execution tracking
        self._active_mission_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    def _configure_logging(self) -> None:
        """Configures structured console logging."""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level, logging.INFO),
            format=self.config.log_format,
            datefmt=self.config.date_format,
            force=True,
        )

    async def start(self) -> None:
        """Boots all subsystems, opens TCP listener, and transitions to STANDBY."""
        logger.info("Boot sequence commencing...")

        # Start TCP Ingest listener
        await self.ingest_server.start()

        # Transition FSM from BOOT to STANDBY
        self.fsm.transition_to(
            PayloadState.STANDBY,
            reason="TCP Ingestion active. Payload awaiting Ground AI Refined CDM."
        )

        logger.info(
            f"Edge Payload operational in STANDBY mode.\n"
            f"  Ingesting from Ground AI on: {self.config.ingest_host}:{self.config.ingest_port}\n"
            f"  Publishing to core_phy on:   {self.config.core_phy_host}:{self.config.core_phy_port}\n"
            f"  Observation Lead Time:       {self.config.observation_lead_time_seconds / 60.0:.1f} minutes\n"
            f"  Immediate Dispatch Mode:     {self.config.immediate_dispatch}"
        )

    async def stop(self) -> None:
        """Gracefully shuts down all active tasks and network sockets."""
        logger.info("Initiating graceful payload shutdown...")
        self._shutdown_event.set()

        if self._active_mission_task and not self._active_mission_task.done():
            logger.info("Canceling active mission execution task...")
            self._active_mission_task.cancel()
            try:
                await self._active_mission_task
            except asyncio.CancelledError:
                pass

        if self.vision.is_active():
            await self.vision.stop_tracking()

        await self.ingest_server.stop()
        await self.slew_publisher.disconnect()

        logger.info("Payload shutdown complete. All sockets closed.")

    async def handle_incoming_cdm(self, cdm: RefinedCDM) -> None:
        """
        Ingestion callback triggered whenever the TCP server receives and parses
        a Refined Conjunction Data Message from Ground AI.
        """
        logger.info(
            f"Processing ingested CDM for Secondary Asset '{cdm.conjunction_data.secondary_asset}'..."
        )

        # Step 5: Address Eclipse Blindness in Ground AI Logic
        if cdm.action == "ABORT_VISION_USE_GROUND_RADAR":
            logger.warning(
                f"[ECLIPSE BLINDNESS DETECTED] Ground AI flagged satellite in Earth umbra (shadow) "
                f"at observation window for '{cdm.conjunction_data.secondary_asset}'. "
                f"Aborting optical tasking to conserve battery and reaction wheel life. "
                f"Bypassing edge payload -> Ground Radar tracking recommended."
            )
            if self._active_mission_task and not self._active_mission_task.done():
                self._active_mission_task.cancel()
            if self.vision.is_active():
                await self.vision.stop_tracking()
            if self.fsm.current_state != PayloadState.STANDBY:
                self.fsm.reset_to_standby(reason="Optical tasking aborted due to Earth umbra eclipse.")
            return

        # Ensure state transition is valid
        if self.fsm.current_state not in (PayloadState.STANDBY, PayloadState.MISSION_COMPLETE):
            logger.warning(
                f"Received CDM while in state [{self.fsm.current_state.value}]. "
                "Rescheduling active conjunction context."
            )
            if self._active_mission_task and not self._active_mission_task.done():
                self._active_mission_task.cancel()

        try:
            if self.fsm.current_state == PayloadState.MISSION_COMPLETE:
                self.fsm.reset_to_standby(reason="New CDM arrived post-mission.")

            # Compute observation window: T_0 = TCA - 30 min
            schedule = self.scheduler.plan_observation(cdm)

            # Transition FSM to TARGET_SCHEDULED
            self.fsm.transition_to(
                PayloadState.TARGET_SCHEDULED,
                reason=f"Observation scheduled for {schedule.target_asset} at {schedule.execute_at_utc.isoformat()}Z"
            )

            # Arm optical sensor interface
            await self.vision.arm_sensor(schedule.target_asset)

            # Spawn asynchronous mission execution task
            self._active_mission_task = asyncio.create_task(
                self._execute_mission_lifecycle(schedule)
            )

        except (PacketValidationError, StateTransitionError) as err:
            logger.error(f"Failed to schedule CDM observation: {err}")
            self.fsm.fault(reason=str(err))
        except Exception as ex:
            logger.exception(f"Unexpected error during CDM processing: {ex}")
            self.fsm.fault(reason=f"Unhandled exception: {ex}")

    async def _execute_mission_lifecycle(self, schedule: ObservationSchedule) -> None:
        """
        Asynchronous mission execution loop:
        1. Waits for observation window T_0 (or bypasses if immediate_dispatch=True).
        2. Transitions FSM to SLEWING.
        3. Pushes SlewCommand over TCP to core_phy.
        4. Transitions FSM to TRACKING and engages optical vision.
        5. Tracks threat across observation window.
        6. Concludes observation and resets to STANDBY.
        """
        target = schedule.target_asset
        try:
            # 1. Wait for observation window T_0
            if not self.config.immediate_dispatch:
                delta_sec = schedule.seconds_until_execution()
                if delta_sec > 0:
                    scaled_sleep = delta_sec / self.config.time_scale_factor
                    logger.info(
                        f"Awaiting observation window for '{target}'. Sleeping {delta_sec:.1f}s "
                        f"(sim-scaled: {scaled_sleep:.1f}s)..."
                    )
                    await asyncio.sleep(scaled_sleep)
                else:
                    logger.warning(
                        f"Observation window T_0 for '{target}' is already in the past "
                        f"({delta_sec:.1f}s). Dispatching immediately."
                    )
            else:
                logger.info(f"[Hackathon Time-Loop] Immediate dispatch enabled. Skipping wait for T_0.")

            # 2. Transition to SLEWING
            self.fsm.transition_to(
                PayloadState.SLEWING,
                reason=f"T_0 reached. Dispatching slew command for target {target} to core_phy."
            )

            # 3. Publish SlewCommand to core_phy
            success = await self.slew_publisher.publish_slew_command(
                schedule.slew_command,
                retry_count=5,
                retry_delay=1.0,
            )

            if not success:
                raise RuntimeError(f"Failed to deliver SlewCommand to core_phy for target {target}.")

            # 4. Transition to TRACKING
            self.fsm.transition_to(
                PayloadState.TRACKING,
                reason=f"Slew command acknowledged by core_phy. Engaging Star Tracker differencing."
            )
            await self.vision.start_tracking()

            # 5. Track during observation window (Phase 2 OpenCV differencing runs here)
            tracking_time = self.config.tracking_duration_seconds / self.config.time_scale_factor
            logger.info(f"Optical tracking active on {target} for {tracking_time:.1f}s...")
            await asyncio.sleep(tracking_time)

            # 6. Mission Complete & Reset
            await self.vision.stop_tracking()
            self.fsm.transition_to(
                PayloadState.MISSION_COMPLETE,
                reason=f"Observation window for {target} successfully completed."
            )

            # Automatically return to STANDBY for next CDM
            reset_delay = 1.0 / self.config.time_scale_factor
            await asyncio.sleep(min(reset_delay, 1.0))
            self.fsm.reset_to_standby(reason="Mission cycle complete. Ready for next CDM.")

        except asyncio.CancelledError:
            logger.info(f"Mission task for target '{target}' was canceled.")
            if self.vision.is_active():
                await self.vision.stop_tracking()
            raise
        except Exception as ex:
            logger.exception(f"Mission execution error for target '{target}': {ex}")
            self.fsm.fault(reason=str(ex))


async def run_payload(args: argparse.Namespace) -> None:
    """Entry point for standalone execution."""
    config = PayloadConfig(
        ingest_host=args.ingest_host,
        ingest_port=args.ingest_port,
        core_phy_host=args.core_phy_host,
        core_phy_port=args.core_phy_port,
        observation_lead_time_seconds=args.lead_time,
        immediate_dispatch=args.immediate,
        time_scale_factor=args.time_scale,
        tracking_duration_seconds=args.tracking_duration,
        log_level=args.log_level,
    )

    manager = PayloadManager(config=config)
    await manager.start()

    # Handle system termination signals
    loop = asyncio.get_running_loop()
    stop_signal = asyncio.Event()

    def _sig_handler():
        logger.info("Termination signal received.")
        stop_signal.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            # Signal handling on Windows / restricted platforms
            pass

    try:
        await stop_signal.wait()
    finally:
        await manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Project Kessler: Edge Processing Payload (Phase 1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ingest-host",
        default="127.0.0.1",
        help="Host IP to bind the Ground AI ingestion listener.",
    )
    parser.add_argument(
        "--ingest-port",
        type=int,
        default=5555,
        help="TCP port to listen for Ground AI Refined CDMs.",
    )
    parser.add_argument(
        "--core-phy-host",
        default="127.0.0.1",
        help="Host IP of the core_phy (Basilisk) physics node.",
    )
    parser.add_argument(
        "--core-phy-port",
        type=int,
        default=5556,
        help="TCP port to publish SlewCommand packets to core_phy.",
    )
    parser.add_argument(
        "--lead-time",
        type=float,
        default=1800.0,
        help="Observation window lead time in seconds before TCA (T_0 = TCA - lead_time).",
    )
    parser.add_argument(
        "--immediate",
        action="store_true",
        help="Hackathon mode: Dispatch SlewCommand immediately without waiting for TCA.",
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="Simulation clock acceleration factor (e.g. 10.0 for 10x speed).",
    )
    parser.add_argument(
        "--tracking-duration",
        type=float,
        default=5.0,
        help="Duration in seconds to maintain optical tracking before completing mission.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level.",
    )

    args = parser.parse_args()

    try:
        asyncio.run(run_payload(args))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated.")


if __name__ == "__main__":
    main()
