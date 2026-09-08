#!/usr/bin/env python3
"""
Laptop 1: Core Physics & Simulator Node
Binds to 0.0.0.0 to allow Edge Node (Laptop 2) to connect over the local network.
"""
import json
import logging
import sys
import os

# Add parent directory to path so we can import core_phy
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core_phy.fsw_server import FSWPhysicsServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [LAPTOP-1-CORE] %(message)s")

def main():
    config_path = os.path.join(os.path.dirname(__file__), 'network_config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)

    # Bind to 0.0.0.0 to accept connections from other laptops
    bind_host = "0.0.0.0"
    cmd_port = config["PORTS"]["core_cmd"]
    stream_port = config["PORTS"]["core_stream"]

    logging.info(f"Starting Core FSW Server on {bind_host}")
    logging.info(f"Command Port: {cmd_port} | Stream Port: {stream_port}")

    server = FSWPhysicsServer(
        bind_host=bind_host,
        cmd_port=cmd_port,
        stream_port=stream_port,
        ephemeris_path="../shared/initial_ephemeris.json"
    )
    
    try:
        server.start()
        logging.info("Core Node running. Press Ctrl+C to stop.")
        # Keep main thread alive
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down Core Node...")
        server.stop()

if __name__ == "__main__":
    main()
