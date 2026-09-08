#!/usr/bin/env python3
"""
Laptop 1: Core Physics & Simulator Node
Binds to 0.0.0.0 to allow Edge Node (Laptop 2) to connect over the local network.
"""
import json
import logging
import os
from pathlib import Path
import sys
import time

# Auto-switch to virtualenv if required packages are present in bsk_env
try:
    import cv2
    import numpy as np
    import Basilisk
except ImportError:
    bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
    if os.path.exists(bsk_python) and sys.executable != bsk_python:
        print(f"[launch_core] Switching to Python virtualenv at {bsk_python}...")
        os.execv(bsk_python, [bsk_python] + sys.argv)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core_phy.fsw_server import FSWPhysicsServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [LAPTOP-1-CORE] %(message)s")

def main():
    config_path = ROOT_DIR / "networked_integration" / "network_config.json"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = json.load(f)
        cmd_port = config["PORTS"]["core_cmd"]
        stream_port = config["PORTS"]["core_stream"]
    else:
        cmd_port = 15556
        stream_port = 15000

    bind_host = "0.0.0.0"
    ephem_path = ROOT_DIR / "shared" / "initial_ephemeris.json"

    logging.info(f"Starting Core FSW Server on {bind_host}")
    logging.info(f"Command Port: {cmd_port} | Stream Port: {stream_port}")
    logging.info(f"Initial Ephemeris Path: {ephem_path}")

    server = FSWPhysicsServer(
        bind_host=bind_host,
        cmd_port=cmd_port,
        stream_port=stream_port,
        ephemeris_path=str(ephem_path),
    )
    
    try:
        server.start()
        logging.info("Core Node running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down Core Node...")
        server.stop()

if __name__ == "__main__":
    main()
