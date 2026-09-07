"""
Project Kessler - Autonomous Collision Avoidance
Upgraded Edge Vision Node (Phase 2: Frame Differencing & Streak Extraction)
-------------------------------------------------------------------------------
Upgraded from the initial prototype:
1. Replaced naive global moments with robust contour filtering (strips point noise).
2. Added running consecutive frame differencing (I_k - I_{k-1}) alongside reference subtraction.
3. Added 2D velocity vector estimation [Vx, Vy] and heading angle for Phase 3 Danger Corridor.
4. CLI configuration for host/port and detection parameters.
"""

import argparse
from datetime import datetime, timezone
import json
import math
import socket
import struct
import sys
import time
import cv2
import numpy as np


def recvall(sock: socket.socket, count: int) -> bytes:
    """Reads exactly count bytes from TCP socket stream."""
    buf = bytearray()
    while count:
        newbuf = sock.recv(count)
        if not newbuf:
            return None
        buf.extend(newbuf)
        count -= len(newbuf)
    return bytes(buf)


def run_edge_node(
    host: str = "127.0.0.1",
    port: int = 5000,
    differencing_mode: str = "consecutive",  # "consecutive", "reference", or "hybrid"
    threshold_val: int = 30,
    min_area: float = 3.0,
):
    print("=" * 70)
    print(" PROJECT KESSLER: EDGE VISION PROCESSOR (PHASE 2 UPGRADE)")
    print("=" * 70)
    print(f"Connecting to video stream source at {host}:{port}...")

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.connect((host, port))
        print(f"Connected to simulator stream on {host}:{port}.")
    except ConnectionRefusedError:
        print(f"Error: Connection refused at {host}:{port}. Is the video stream running?", file=sys.stderr)
        return

    reference_frame = None
    previous_frame = None
    attitude_locked = False
    centroid_history = []  # List of (timestamp, x, y)
    frame_idx = 0

    print("Listening for simulator protocol: 5-byte header [1B type, 4B len]...")

    while True:
        header = recvall(client_socket, 5)
        if header is None:
            print("Stream closed by simulator server.")
            break

        msg_type = header[0]
        payload_len = struct.unpack(">I", header[1:5])[0]
        payload = recvall(client_socket, payload_len)

        if payload is None:
            print("Stream terminated unexpectedly.")
            break

        if msg_type == 0:
            # Control Message (JSON)
            try:
                data = json.loads(payload.decode("utf-8"))
                if data.get("status") == "LOCKED":
                    attitude_locked = True
                    print("[FSW Status] Attitude lock confirmed! Engaging frame differencing.")
            except Exception as e:
                print(f"Error parsing control message: {e}", file=sys.stderr)

        elif msg_type == 1:
            # Image Frame (JPEG/PNG)
            if not attitude_locked:
                continue

            frame_idx += 1
            now_sec = time.time()
            nparr = np.frombuffer(payload, dtype=np.uint8)
            current_frame = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

            if current_frame is None:
                continue

            # First frame initializes reference
            if reference_frame is None:
                reference_frame = current_frame.copy()
                previous_frame = current_frame.copy()
                print(f"[Frame {frame_idx}] Static reference starfield established ({current_frame.shape[1]}x{current_frame.shape[0]}).")
                continue

            # 1. Background Subtraction
            if differencing_mode == "consecutive":
                diff_frame = cv2.absdiff(previous_frame, current_frame)
            elif differencing_mode == "reference":
                diff_frame = cv2.absdiff(reference_frame, current_frame)
            else:  # hybrid
                d_ref = cv2.absdiff(reference_frame, current_frame)
                d_prev = cv2.absdiff(previous_frame, current_frame)
                diff_frame = cv2.bitwise_or(d_ref, d_prev)

            previous_frame = current_frame.copy()

            # 2. Thresholding & Morphological Noise Stripping
            _, thresh = cv2.threshold(diff_frame, threshold_val, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            filtered = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

            # 3. Contour Extraction (Fixed: replaces global moments)
            contours, _ = cv2.findContours(filtered, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            best_cnt = None
            max_score = 0.0
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < min_area:
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = max(w, h) / max(1, min(w, h))
                score = area * math.sqrt(aspect_ratio)
                if score > max_score:
                    max_score = score
                    best_cnt = cnt

            if best_cnt is not None:
                moments = cv2.moments(best_cnt)
                if moments["m00"] > 0:
                    cX = float(moments["m10"] / moments["m00"])
                    cY = float(moments["m01"] / moments["m00"])
                    bx, by, bw, bh = cv2.boundingRect(best_cnt)
                    streak_len = float(math.hypot(bw, bh))

                    # 4. Trajectory & Velocity Vector Estimation (Phase 3 input)
                    vx, vy, speed, heading_deg = None, None, None, None
                    if centroid_history:
                        prev_t, prev_x, prev_y = centroid_history[-1]
                        dt = now_sec - prev_t
                        if dt > 0.001:
                            vx = round((cX - prev_x) / dt, 2)
                            vy = round((cY - prev_y) / dt, 2)
                            speed = round(math.hypot(vx, vy), 2)
                            heading_deg = round(math.degrees(math.atan2(vy, vx)), 2)

                    centroid_history.append((now_sec, cX, cY))

                    output = {
                        "status": "DEBRIS_DETECTED",
                        "frame_index": frame_idx,
                        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "centroid_x": round(cX, 2),
                        "centroid_y": round(cY, 2),
                        "bounding_box": [bx, by, bw, bh],
                        "streak_length_px": round(streak_len, 2),
                        "velocity_vector": {"vx_px_s": vx, "vy_px_s": vy, "speed_px_s": speed},
                        "heading_angle_deg": heading_deg,
                        "history_count": len(centroid_history),
                    }
                    print(json.dumps(output))

    client_socket.close()


def main():
    parser = argparse.ArgumentParser(description="Project Kessler: Upgraded Phase 2 Edge Vision Node")
    parser.add_argument("--host", default="127.0.0.1", help="Video stream host IP")
    parser.add_argument("--port", type=int, default=5000, help="Video stream TCP port")
    parser.add_argument(
        "--mode",
        default="consecutive",
        choices=["consecutive", "reference", "hybrid"],
        help="Background differencing mode",
    )
    parser.add_argument("--threshold", type=int, default=30, help="Pixel difference threshold")
    parser.add_argument("--min-area", type=float, default=3.0, help="Minimum contour pixel area")

    args = parser.parse_args()
    run_edge_node(
        host=args.host,
        port=args.port,
        differencing_mode=args.mode,
        threshold_val=args.threshold,
        min_area=args.min_area,
    )


if __name__ == "__main__":
    main()
