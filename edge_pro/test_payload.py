"""
Project Kessler - Edge Processing Payload
Comprehensive Unit & Integration Test Suite (Phase 1)
-------------------------------------------------------------------------------
Executes automated validation for:
1. JSON Contract parsing, serialization, and strict schema validation
   including Ground-AI-provided observation_window_start_utc.
2. ISO 8601 UTC timestamp parsing and direct pass-through to SlewCommand.
3. Eclipse blindness detection and abort handling (ABORT_VISION_USE_GROUND_RADAR).
4. Payload Finite State Machine (FSM) transitions and safety invariants.
5. Full End-to-End TCP Loopback between Ground AI, edge_pro, and core_phy.
"""

import asyncio
from datetime import datetime, timezone
import json
import unittest

from edge_pro.config import PayloadConfig
from edge_pro.models import (
    RefinedCDM,
    CDMHeader,
    ConjunctionData,
    AIDragPrediction,
    SlewCommand,
    PacketValidationError,
)
from edge_pro.scheduler import (
    parse_iso8601_utc,
    format_iso8601_utc,
    ObservationScheduler,
)
from edge_pro.state_machine import (
    PayloadStateMachine,
    PayloadState,
    StateTransitionError,
)
from edge_pro.payload_manager import PayloadManager
from edge_pro.vision_interface import MockVisionPipeline


class TestPacketContracts(unittest.TestCase):
    """Verifies strict adherence to Phase 1 JSON contracts."""

    def setUp(self):
        self.valid_input_json = json.dumps({
            "header": {
                "type": "REFINED_CDM",
                "timestamp_utc": "2026-09-08T12:00:00Z"
            },
            "conjunction_data": {
                "primary_asset": "Kessler_Sat_1",
                "secondary_asset": "Debris_Obj_8492",
                "time_of_closest_approach": "2026-09-08T14:32:15Z",
                "observation_window_start_utc": "2026-09-08T13:00:15Z"
            }
        })

        self.extended_input_json = json.dumps({
            "header": {
                "type": "REFINED_CDM",
                "timestamp_utc": "2026-09-08T12:00:00Z"
            },
            "conjunction_data": {
                "primary_asset": "Kessler_Sat_1",
                "secondary_asset": "Debris_Obj_8492",
                "time_of_closest_approach": "2026-09-08T14:32:15Z",
                "observation_window_start_utc": "2026-09-08T13:00:15Z",
                "miss_distance_km": 1.2
            },
            "ai_drag_prediction": {
                "f107_flux": 185.4,
                "kp_index": 5.2,
                "drag_multiplier": 1.45,
                "ellipsoid_covariance_matrix": [150.0, 50.0, 50.0]
            },
            "action": "RECOMMEND_OPTICAL_CONFIRMATION"
        })

    def test_valid_input_parsing(self):
        """Validates that standard Ground AI Refined CDM is parsed accurately."""
        cdm = RefinedCDM.from_json(self.valid_input_json)
        self.assertEqual(cdm.header.type, "REFINED_CDM")
        self.assertEqual(cdm.header.timestamp_utc, "2026-09-08T12:00:00Z")
        self.assertEqual(cdm.conjunction_data.primary_asset, "Kessler_Sat_1")
        self.assertEqual(cdm.conjunction_data.secondary_asset, "Debris_Obj_8492")
        self.assertEqual(cdm.conjunction_data.time_of_closest_approach, "2026-09-08T14:32:15Z")
        self.assertEqual(cdm.conjunction_data.observation_window_start_utc, "2026-09-08T13:00:15Z")

    def test_extended_input_parsing(self):
        """Validates Ground AI CDM with extended AI drag telemetry."""
        cdm = RefinedCDM.from_json(self.extended_input_json)
        self.assertEqual(cdm.conjunction_data.miss_distance_km, 1.2)
        self.assertIsNotNone(cdm.ai_drag_prediction)
        self.assertEqual(cdm.ai_drag_prediction.drag_multiplier, 1.45)
        self.assertEqual(cdm.ai_drag_prediction.ellipsoid_covariance_matrix, [150.0, 50.0, 50.0])
        self.assertEqual(cdm.action, "RECOMMEND_OPTICAL_CONFIRMATION")

    def test_missing_observation_window_start_rejected(self):
        """Ensures CDM without observation_window_start_utc is rejected."""
        bad_json = json.dumps({
            "header": {"type": "REFINED_CDM", "timestamp_utc": "2026-09-08T12:00:00Z"},
            "conjunction_data": {
                "primary_asset": "Kessler_Sat_1",
                "secondary_asset": "Debris_Obj_8492",
                "time_of_closest_approach": "2026-09-08T14:32:15Z"
                # Missing observation_window_start_utc
            }
        })
        with self.assertRaises(PacketValidationError):
            RefinedCDM.from_json(bad_json)

    def test_invalid_header_type_rejected(self):
        """Ensures non-REFINED_CDM packet headers raise PacketValidationError."""
        bad_json = json.dumps({
            "header": {"type": "RAW_RADAR_TRACK", "timestamp_utc": "2026-09-08T12:00:00Z"},
            "conjunction_data": {
                "primary_asset": "Kessler_Sat_1",
                "secondary_asset": "Debris_Obj_8492",
                "time_of_closest_approach": "2026-09-08T14:32:15Z",
                "observation_window_start_utc": "2026-09-08T13:00:15Z"
            }
        })
        with self.assertRaises(PacketValidationError):
            RefinedCDM.from_json(bad_json)

    def test_slew_command_contract(self):
        """Validates output SlewCommand structure matching core_phy specification."""
        cmd = SlewCommand(
            command="SLEW_TO_TARGET",
            target_asset="Debris_Obj_8492",
            execute_at_utc="2026-09-08T13:00:15Z",
            sensor_mode="OPTICAL_TRACKING",
        )
        cmd_dict = cmd.to_dict()
        self.assertEqual(cmd_dict["command"], "SLEW_TO_TARGET")
        self.assertEqual(cmd_dict["target_asset"], "Debris_Obj_8492")
        self.assertEqual(cmd_dict["execute_at_utc"], "2026-09-08T13:00:15Z")
        self.assertEqual(cmd_dict["sensor_mode"], "OPTICAL_TRACKING")


class TestTimestampAndSchedulerPassThrough(unittest.TestCase):
    """Verifies Ground AI observation window pass-through and timezone handling."""

    def test_observation_window_pass_through_exact(self):
        """Verifies Edge Payload passes Ground-AI observation_window_start_utc directly to SlewCommand."""
        cdm_data = {
            "header": {"type": "REFINED_CDM", "timestamp_utc": "2026-09-08T12:00:00Z"},
            "conjunction_data": {
                "primary_asset": "Kessler_Sat_1",
                "secondary_asset": "Debris_Obj_8492",
                "time_of_closest_approach": "2026-09-08T14:32:15Z",
                "observation_window_start_utc": "2026-09-08T13:00:15Z"  # Ground AI 92-min lead
            }
        }
        cdm = RefinedCDM.from_dict(cdm_data)
        scheduler = ObservationScheduler(immediate_dispatch=True)
        schedule = scheduler.plan_observation(cdm)

        # Ensure exact pass-through with no edge calculation alteration
        self.assertEqual(schedule.slew_command.execute_at_utc, "2026-09-08T13:00:15Z")
        self.assertEqual(schedule.target_asset, "Debris_Obj_8492")
        self.assertEqual(schedule.lead_time_seconds, 5520.0)  # exactly 92 minutes

    def test_timezone_normalization_to_utc(self):
        """Verifies foreign timezone offsets (+05:30) normalize accurately to UTC."""
        ts_with_offset = "2026-09-08T20:02:15+05:30"
        dt_utc = parse_iso8601_utc(ts_with_offset)
        self.assertEqual(dt_utc.tzinfo, timezone.utc)
        self.assertEqual(dt_utc.hour, 14)
        self.assertEqual(dt_utc.minute, 32)
        self.assertEqual(dt_utc.second, 15)


class TestPayloadStateMachine(unittest.TestCase):
    """Verifies finite state machine lifecycle and constraint enforcement."""

    def test_nominal_lifecycle(self):
        """Tests BOOT -> STANDBY -> TARGET_SCHEDULED -> SLEWING -> TRACKING -> MISSION_COMPLETE -> STANDBY."""
        fsm = PayloadStateMachine(initial_state=PayloadState.BOOT)
        self.assertEqual(fsm.current_state, PayloadState.BOOT)

        fsm.transition_to(PayloadState.STANDBY)
        self.assertEqual(fsm.current_state, PayloadState.STANDBY)

        fsm.transition_to(PayloadState.TARGET_SCHEDULED)
        self.assertEqual(fsm.current_state, PayloadState.TARGET_SCHEDULED)

        fsm.transition_to(PayloadState.SLEWING)
        self.assertEqual(fsm.current_state, PayloadState.SLEWING)

        fsm.transition_to(PayloadState.TRACKING)
        self.assertEqual(fsm.current_state, PayloadState.TRACKING)

        fsm.transition_to(PayloadState.MISSION_COMPLETE)
        self.assertEqual(fsm.current_state, PayloadState.MISSION_COMPLETE)

        fsm.reset_to_standby()
        self.assertEqual(fsm.current_state, PayloadState.STANDBY)

    def test_illegal_transition_rejection(self):
        """Ensures jumping directly from BOOT to TRACKING raises StateTransitionError."""
        fsm = PayloadStateMachine(initial_state=PayloadState.BOOT)
        with self.assertRaises(StateTransitionError):
            fsm.transition_to(PayloadState.TRACKING)


class TestEndToEndLoopbackAndEclipse(unittest.IsolatedAsyncioTestCase):
    """
    Spawns in-memory TCP servers and validates full data flow including Ground AI pass-through
    and Eclipse Blindness abort handling.
    """

    async def test_full_pipeline_immediate_dispatch(self):
        received_slew_packets = []
        core_phy_port = 15556
        ingest_port = 15555

        # 1. Start Mock core_phy listener
        async def handle_core_phy(reader, writer):
            data = await reader.readuntil(b"\n")
            packet = json.loads(data.decode("utf-8").strip())
            received_slew_packets.append(packet)
            writer.close()
            await writer.wait_closed()

        core_phy_server = await asyncio.start_server(handle_core_phy, "127.0.0.1", core_phy_port)

        # 2. Start PayloadManager in immediate dispatch mode
        config = PayloadConfig(
            ingest_host="127.0.0.1",
            ingest_port=ingest_port,
            core_phy_host="127.0.0.1",
            core_phy_port=core_phy_port,
            immediate_dispatch=True,
            time_scale_factor=10.0,
            tracking_duration_seconds=0.1,
            log_level="WARNING",
        )
        manager = PayloadManager(config=config)
        await manager.start()

        try:
            # 3. Transmit Ground AI Refined CDM with observation_window_start_utc
            cdm_payload = {
                "header": {
                    "type": "REFINED_CDM",
                    "timestamp_utc": "2026-09-08T12:00:00Z"
                },
                "conjunction_data": {
                    "primary_asset": "Kessler_Sat_1",
                    "secondary_asset": "Debris_Obj_8492",
                    "time_of_closest_approach": "2026-09-08T14:32:15Z",
                    "observation_window_start_utc": "2026-09-08T13:00:15Z"  # 92 min prior
                }
            }
            wire_data = (json.dumps(cdm_payload) + "\n").encode("utf-8")

            reader, writer = await asyncio.open_connection("127.0.0.1", ingest_port)
            writer.write(wire_data)
            await writer.drain()

            ack_data = await reader.readuntil(b"\n")
            ack = json.loads(ack_data.decode("utf-8").strip())
            self.assertEqual(ack.get("status"), "ACK")
            writer.close()
            await writer.wait_closed()

            # 4. Wait for async mission execution loop to complete
            for _ in range(25):
                if manager.fsm.current_state == PayloadState.STANDBY and len(received_slew_packets) > 0:
                    break
                await asyncio.sleep(0.1)

            # 5. Verify core_phy received exact SlewCommand with Ground-AI timestamp
            self.assertEqual(len(received_slew_packets), 1)
            cmd = received_slew_packets[0]
            self.assertEqual(cmd["command"], "SLEW_TO_TARGET")
            self.assertEqual(cmd["target_asset"], "Debris_Obj_8492")
            self.assertEqual(cmd["execute_at_utc"], "2026-09-08T13:00:15Z")
            self.assertEqual(cmd["sensor_mode"], "OPTICAL_TRACKING")

            # 6. Verify FSM completed the cycle
            self.assertEqual(manager.fsm.current_state, PayloadState.STANDBY)

        finally:
            await manager.stop()
            core_phy_server.close()
            await core_phy_server.wait_closed()

    async def test_eclipse_blindness_abort(self):
        """Verifies that Ground AI action ABORT_VISION_USE_GROUND_RADAR aborts optical tasking."""
        ingest_port = 15557
        config = PayloadConfig(
            ingest_host="127.0.0.1",
            ingest_port=ingest_port,
            core_phy_host="127.0.0.1",
            core_phy_port=15558,
            immediate_dispatch=True,
            log_level="WARNING",
        )
        manager = PayloadManager(config=config)
        await manager.start()

        try:
            eclipse_cdm = {
                "header": {"type": "REFINED_CDM", "timestamp_utc": "2026-09-08T12:00:00Z"},
                "conjunction_data": {
                    "primary_asset": "Kessler_Sat_1",
                    "secondary_asset": "Debris_Obj_8492",
                    "time_of_closest_approach": "2026-09-08T14:32:15Z",
                    "observation_window_start_utc": "2026-09-08T13:00:15Z"
                },
                "action": "ABORT_VISION_USE_GROUND_RADAR"  # Ground AI flagged Earth umbra shadow!
            }
            wire_data = (json.dumps(eclipse_cdm) + "\n").encode("utf-8")

            reader, writer = await asyncio.open_connection("127.0.0.1", ingest_port)
            writer.write(wire_data)
            await writer.drain()

            ack_data = await reader.readuntil(b"\n")
            ack = json.loads(ack_data.decode("utf-8").strip())
            self.assertEqual(ack.get("status"), "ACK")
            writer.close()
            await writer.wait_closed()

            await asyncio.sleep(0.2)

            # Edge payload must stay in STANDBY, optical tracking is NOT engaged, power conserved!
            self.assertEqual(manager.fsm.current_state, PayloadState.STANDBY)
            self.assertFalse(manager.vision.is_active())

        finally:
            await manager.stop()


if __name__ == "__main__":
    unittest.main()
