"""
Project Kessler - Mock Ground AI Node
-------------------------------------------------------------------------------
Simulates Ground AI uplinking a Refined Conjunction Data Message (CDM)
over a local TCP socket to the Edge Processing Payload.
Ground AI owns orbital mechanics, Line-of-Sight periodicity (TCA - N * T_orbit),
and Earth-umbra eclipse shadow checks.

Usage:
  python3 -m edge_pro.mock_ground_ai [--host 127.0.0.1] [--port 5555]
  python3 -m edge_pro.mock_ground_ai --abort-eclipse
"""

import argparse
from datetime import datetime, timezone, timedelta
import json
import socket
import sys
import time

# Default observation window is exactly 1 orbital period (~92 min for 400km LEO) prior to TCA
DEFAULT_CDM = {
    "header": {
        "type": "REFINED_CDM",
        "timestamp_utc": "2026-09-08T12:00:00Z"
    },
    "conjunction_data": {
        "primary_asset": "Kessler_Sat_1",
        "secondary_asset": "Debris_Obj_8492",
        "time_of_closest_approach": "2026-09-08T14:32:15Z",
        "observation_window_start_utc": "2026-09-08T13:00:15Z",  # Exactly TCA - 92 minutes
        "miss_distance_km": 0.1,
    },
    "ai_drag_prediction": {
        "f107_flux": 178.2,
        "kp_index": 4.1,
        "drag_multiplier": 1.32,
        "ellipsoid_covariance_matrix": [120.0, 45.0, 45.0],
    },
    "action": "RECOMMEND_OPTICAL_CONFIRMATION",
}


def send_refined_cdm(
    host: str = "127.0.0.1",
    port: int = 5555,
    payload: dict = None,
    timeout: float = 5.0,
) -> bool:
    """Connects to edge_pro ingestion server and transmits a Refined CDM packet."""
    cdm_data = payload or DEFAULT_CDM
    wire_bytes = (json.dumps(cdm_data) + "\n").encode("utf-8")

    print(f"[Mock Ground AI] Connecting to Edge Payload at {host}:{port}...")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            print(f"[Mock Ground AI] Transmitting Refined CDM:\n{json.dumps(cdm_data, indent=2)}")
            sock.sendall(wire_bytes)

            # Await acknowledgment
            sock.settimeout(timeout)
            response = sock.recv(1024)
            if response:
                print(f"[Mock Ground AI] Received Response: {response.decode('utf-8').strip()}")
            return True
    except ConnectionRefusedError:
        print(f"[Mock Ground AI] Error: Connection refused at {host}:{port}. Is edge_pro running?", file=sys.stderr)
        return False
    except Exception as ex:
        print(f"[Mock Ground AI] Transmission error: {ex}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Mock Ground AI Node for Project Kessler")
    parser.add_argument("--host", default="127.0.0.1", help="Target host")
    parser.add_argument("--port", type=int, default=5555, help="Target port")
    parser.add_argument("--primary", default="Kessler_Sat_1", help="Primary asset name")
    parser.add_argument("--secondary", default="Debris_Obj_8492", help="Secondary asset name")
    parser.add_argument("--tca", default="2026-09-08T14:32:15Z", help="TCA ISO 8601 UTC timestamp")
    parser.add_argument(
        "--obs-start",
        default="2026-09-08T13:00:15Z",
        help="Ground-AI-computed observation window start UTC (e.g. TCA - 92 min)",
    )
    parser.add_argument(
        "--abort-eclipse",
        action="store_true",
        help="Simulate Earth-umbra eclipse blindness (action: ABORT_VISION_USE_GROUND_RADAR)",
    )

    args = parser.parse_args()

    action = "ABORT_VISION_USE_GROUND_RADAR" if args.abort_eclipse else "RECOMMEND_OPTICAL_CONFIRMATION"

    payload = {
        "header": {
            "type": "REFINED_CDM",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "conjunction_data": {
            "primary_asset": args.primary,
            "secondary_asset": args.secondary,
            "time_of_closest_approach": args.tca,
            "observation_window_start_utc": args.obs_start,
            "miss_distance_km": 0.1,
        },
        "action": action,
    }

    success = send_refined_cdm(args.host, args.port, payload)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
