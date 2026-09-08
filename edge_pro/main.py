"""
Project Kessler — Edge Processing Payload Service (edge_pro) — Device 2
-------------------------------------------------------------------------------
Main entry point for running the Edge Compute Payload on Device 2 (or embedded board):
1. Ingests Refined CDMs from Ground AI (TCP 5555).
2. Schedules observation windows and transmits SlewCommands to core_phy (TCP 5556).
3. Mitigates ADCS reaction wheel jitter with a mandatory settling delay.
4. Executes frame differencing and streak centroid extraction.
5. Verifies Danger Corridor breach via Liang-Barsky directed ray-AABB clipping.
6. Executes Active-Active negotiation scoring and minimal Delta-V burn optimization.
7. Publishes decision telemetry (TCP 5560) and 2D Software Reality HUD frames.
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys
import time

from edge_pro.config import PayloadConfig
from edge_pro.payload_manager import PayloadManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [edge_pro.main] %(message)s",
)
logger = logging.getLogger("edge_pro.main")


def parse_args():
    parser = argparse.ArgumentParser(description="Project Kessler — Edge Processing Payload (Device 2)")
    parser.add_argument("--bind-host", default="0.0.0.0", help="Address to bind Ingestion Server (default: 0.0.0.0)")
    parser.add_argument("--ingest-port", type=int, default=5555, help="Port for Ground AI ingestion (default: 5555)")
    parser.add_argument("--core-phy-host", default="127.0.0.1", help="Target core_phy FSW host (default: 127.0.0.1)")
    parser.add_argument("--core-phy-port", type=int, default=5556, help="Target core_phy FSW command port (default: 5556)")
    parser.add_argument("--stream-host", default="127.0.0.1", help="Target core_phy optical stream host (default: 127.0.0.1)")
    parser.add_argument("--stream-port", type=int, default=5000, help="Target core_phy optical stream port (default: 5000)")
    parser.add_argument("--dashboard-host", default="127.0.0.1", help="Target AOID Dashboard host (default: 127.0.0.1)")
    parser.add_argument("--dashboard-port", type=int, default=5560, help="Target AOID Dashboard port (default: 5560)")
    parser.add_argument("--settling-delay", type=float, default=6.0, help="ADCS reaction wheel jitter settling delay (sec)")
    parser.add_argument("--tracking-duration", type=float, default=10.0, help="Optical tracking duration (sec)")
    parser.add_argument("--immediate-dispatch", action="store_true", help="Bypass wall-clock wait for observation window T_0")
    parser.add_argument("--time-scale", type=float, default=1.0, help="Mission clock acceleration factor (default: 1.0)")
    return parser.parse_args()


async def run_service(args):
    config = PayloadConfig(
        ingest_host=args.bind_host,
        ingest_port=args.ingest_port,
        core_phy_host=args.core_phy_host,
        core_phy_port=args.core_phy_port,
        stream_host=args.stream_host,
        stream_port=args.stream_port,
        dashboard_host=args.dashboard_host,
        dashboard_port=args.dashboard_port,
        adcs_settling_delay_seconds=args.settling_delay,
        tracking_duration_seconds=args.tracking_duration,
        immediate_dispatch=args.immediate_dispatch,
        time_scale_factor=args.time_scale,
    )

    logger.info("Initializing Edge Processing Payload on Device 2...")
    logger.info("  - Ingestion Listener: %s:%d", config.ingest_host, config.ingest_port)
    logger.info("  - core_phy FSW Target: %s:%d", config.core_phy_host, config.core_phy_port)
    logger.info("  - Optical Stream Source: %s:%d", config.stream_host, config.stream_port)
    logger.info("  - AOID Dashboard Target: %s:%d", config.dashboard_host, config.dashboard_port)
    logger.info("  - ADCS Settling Delay: %.1fs", config.adcs_settling_delay_seconds)

    manager = PayloadManager(config=config)
    await manager.start()
    logger.info("Edge Processing Payload is ACTIVE. Awaiting Ground AI Refined CDMs...")

    try:
        while True:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down Edge Processing Payload...")
        await manager.stop()
        logger.info("Edge Processing Payload terminated.")


def main():
    args = parse_args()
    try:
        asyncio.run(run_service(args))
    except KeyboardInterrupt:
        logger.info("Process interrupted by user.")


if __name__ == "__main__":
    main()
