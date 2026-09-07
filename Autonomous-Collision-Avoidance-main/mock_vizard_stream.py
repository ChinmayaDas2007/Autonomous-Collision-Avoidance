"""
Project Kessler - Autonomous Collision Avoidance
Mock Vizard Optical Stream Generator (Phase 2 Test Harness)
-------------------------------------------------------------------------------
Streams synthetic Star Tracker optical video frames to the Edge Vision Node:
1. Emits 5-byte framed control message: {"status": "LOCKED"}
2. Renders a realistic deep space frame with 50 static background stars.
3. Injects a moving high-velocity debris streak moving across the field of view.
"""

import argparse
import json
import socket
import struct
import time
import cv2
import numpy as np


def send_msg(conn: socket.socket, msg_type: int, payload: bytes):
    """
    Sends 5-byte header followed by payload:
      msg_type: 1 byte (0=JSON, 1=Image)
      payload_len: 4 bytes big-endian (>I)
    """
    header = struct.pack(">BI", msg_type, len(payload))
    conn.sendall(header + payload)


def run_mock_server(host: str = "127.0.0.1", port: int = 5000, num_frames: int = 35, frame_rate: float = 10.0):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)

    print("=" * 70)
    print(f" [MOCK VIZARD] Optical Camera Stream Server listening on {host}:{port}...")
    print("=" * 70)

    try:
        conn, addr = server.accept()
        print(f"[MOCK VIZARD] Edge Vision client connected from {addr}")

        # 1. Send LOCKED status
        print("[MOCK VIZARD] Broadcasting attitude status: LOCKED...")
        status_msg = json.dumps({"status": "LOCKED"}).encode("utf-8")
        send_msg(conn, 0, status_msg)
        time.sleep(0.5)

        # 2. Render static starry background
        width, height = 640, 480
        frame = np.zeros((height, width), dtype=np.uint8)

        # Draw 50 static Tycho catalog stars
        np.random.seed(42)
        for _ in range(50):
            x, y = np.random.randint(5, width - 5), np.random.randint(5, height - 5)
            brightness = int(np.random.randint(80, 240))
            cv2.circle(frame, (x, y), 1, brightness, -1)

        # Send reference frame
        print("[MOCK VIZARD] Streaming baseline starfield frame...")
        _, buf = cv2.imencode(".jpg", frame)
        send_msg(conn, 1, buf.tobytes())
        time.sleep(1.0 / frame_rate)

        # 3. Stream frames with moving high-velocity debris streak
        print(f"[MOCK VIZARD] Streaming {num_frames} frames with moving debris streak...")
        streak_x, streak_y = 120.0, 100.0
        vx, vy = 12.0, 7.5  # pixels per frame

        for f_idx in range(num_frames):
            streak_frame = frame.copy()

            # Draw elongated debris streak representing motion blur during exposure
            x0 = int(round(streak_x))
            y0 = int(round(streak_y))
            x1 = int(round(streak_x + vx * 0.8))
            y1 = int(round(streak_y + vy * 0.8))

            cv2.line(streak_frame, (x0, y0), (x1, y1), 255, 2)
            cv2.circle(streak_frame, (x1, y1), 2, 255, -1)

            _, buf = cv2.imencode(".jpg", streak_frame)
            send_msg(conn, 1, buf.tobytes())

            streak_x += vx
            streak_y += vy
            time.sleep(1.0 / frame_rate)

        print("[MOCK VIZARD] Simulation stream complete.")

    except (BrokenPipeError, ConnectionResetError):
        print("[MOCK VIZARD] Client disconnected.")
    finally:
        server.close()


def main():
    parser = argparse.ArgumentParser(description="Mock Vizard Optical Stream Generator")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host")
    parser.add_argument("--port", type=int, default=5000, help="Binding TCP port")
    parser.add_argument("--frames", type=int, default=35, help="Number of frames to stream")
    parser.add_argument("--fps", type=float, default=10.0, help="Frames per second")

    args = parser.parse_args()
    run_mock_server(host=args.host, port=args.port, num_frames=args.frames, frame_rate=args.fps)


if __name__ == "__main__":
    main()
