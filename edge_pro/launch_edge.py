#!/usr/bin/env python3
"""
Laptop 2 / Dual-Role Node: Edge Processing Payload Service (edge_pro)
Binds ingestion listener to 0.0.0.0:15555 for Ground AI uplink.
Connects to core_phy on local loopback (127.0.0.1).
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import sys

# Auto-switch to virtualenv if bsk_env exists
bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
if os.path.exists(bsk_python) and sys.executable != bsk_python:
    os.execv(bsk_python, [bsk_python] + sys.argv)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from edge_pro.config import PayloadConfig
from edge_pro.payload_manager import PayloadManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [LAPTOP-2-EDGE] %(message)s",
)
logger = logging.getLogger("edge_pro.launch")


async def main():
    config_path = ROOT_DIR / "networked_integration" / "network_config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            net_config = json.load(f)
        cmd_port = net_config["PORTS"].get("core_cmd", 15556)
        stream_port = net_config["PORTS"].get("core_stream", 15000)
        ingest_port = net_config["PORTS"].get("edge_ingest", 15555)
    else:
        cmd_port = 15556
        stream_port = 15000
        ingest_port = 15555

    # Core is on same machine -> 127.0.0.1; Ingest binds to 0.0.0.0
    config = PayloadConfig(
        ingest_host="0.0.0.0",
        ingest_port=ingest_port,
        core_phy_host="127.0.0.1",
        core_phy_port=cmd_port,
        stream_host="127.0.0.1",
        stream_port=stream_port,
        dashboard_host="127.0.0.1",
        dashboard_port=16000,
        adcs_settling_delay_seconds=2.0,
        tracking_duration_seconds=4.0,
        immediate_dispatch=True,
        time_scale_factor=1.0,
        processed_frames_dir=ROOT_DIR / "shared" / "processed_frames",
    )

    logger.info("Initializing Edge Processing Payload...")
    logger.info("  - Ingestion Listener: %s:%d (Awaiting Ground AI uplink)", config.ingest_host, config.ingest_port)
    logger.info("  - core_phy Target: %s:%d", config.core_phy_host, config.core_phy_port)
    logger.info("  - Optical Stream: %s:%d", config.stream_host, config.stream_port)
    logger.info("  - WebSocket Broadcaster: port 8002")

    manager = PayloadManager(config=config)
    await manager.start()
    logger.info("Edge Processing Payload is ACTIVE and waiting for Ground AI uplink...")

    # Keep running indefinitely
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Shutting down Edge Processing Payload...")
        await manager.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Edge Node stopped by user.")
