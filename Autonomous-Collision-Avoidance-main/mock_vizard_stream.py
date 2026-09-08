import socket
import json
import struct
import cv2
import numpy as np
import time

HOST = '127.0.0.1'
PORT = 5000

def send_msg(conn, msg_type, payload):
    header = struct.pack('>BI', msg_type, len(payload))
    conn.sendall(header + payload)

def run_mock_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(1)
    
    print(f"Mock server listening on {HOST}:{PORT}...")
    conn, addr = server.accept()
    print(f"Connection from {addr}")

    try:
        # 1. Send LOCKED status
        print("Sending LOCKED status...")
        status_msg = json.dumps({"status": "LOCKED"}).encode('utf-8')
        send_msg(conn, 0, status_msg)
        time.sleep(1)

        # 2. Stream static reference frame
        print("Streaming static frame...")
        frame = np.zeros((480, 640), dtype=np.uint8)
        for i in range(50):
            x, y = np.random.randint(0, 640), np.random.randint(0, 480)
            cv2.circle(frame, (x, y), 1, 255, -1)
            
        _, buf = cv2.imencode('.jpg', frame)
        send_msg(conn, 1, buf.tobytes())
        time.sleep(1)
        
        # 3. Simulate a MISS streak (starts at top left and moves rightwards across top)
        print("Simulation 1: Miss Streak")
        streak_x, streak_y = 50, 50
        for _ in range(15):
            streak_frame = frame.copy()
            cv2.circle(streak_frame, (streak_x, streak_y), 3, 255, -1)
            _, buf = cv2.imencode('.jpg', streak_frame)
            send_msg(conn, 1, buf.tobytes())
            streak_x += 15
            streak_y += 2
            time.sleep(0.1)

        time.sleep(2)
        
        # 4. Simulate a HIT streak (starts top left and moves towards center (320, 240) where corridor is)
        print("Simulation 2: Hit Streak")
        streak_x, streak_y = 50, 50
        for _ in range(15):
            streak_frame = frame.copy()
            cv2.circle(streak_frame, (streak_x, streak_y), 3, 255, -1)
            _, buf = cv2.imencode('.jpg', streak_frame)
            send_msg(conn, 1, buf.tobytes())
            streak_x += 18
            streak_y += 12 # moves sharply into the center where the corridor lives
            time.sleep(0.1)

    except BrokenPipeError:
        print("Client disconnected.")
    finally:
        conn.close()
        server.close()

if __name__ == "__main__":
    run_mock_server()
