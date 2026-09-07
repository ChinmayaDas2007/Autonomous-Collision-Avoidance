import socket
import json
import struct
import cv2
import numpy as np
import math

HOST = '127.0.0.1'
PORT = 5000

# Camera Intrinsics
IMG_W = 640
IMG_H = 480
FOV_DEG = 15.0
FOCAL_LENGTH = (IMG_W / 2.0) / math.tan(math.radians(FOV_DEG / 2.0))
CX = IMG_W / 2.0
CY = IMG_H / 2.0

# 3D Safety Volume (in meters) assuming centered at stand-off Z = 10000m (10 km)
# 1km x 1km box
SAFETY_BOX_HALF_W = 500.0
SAFETY_BOX_HALF_H = 500.0
Z_DEPTH = 10000.0

def project_corridor():
    # Project the 4 corners of the 1km x 1km box at Z_DEPTH
    pts_3d = [
        (-SAFETY_BOX_HALF_W, -SAFETY_BOX_HALF_H, Z_DEPTH),
        ( SAFETY_BOX_HALF_W, -SAFETY_BOX_HALF_H, Z_DEPTH),
        (-SAFETY_BOX_HALF_W,  SAFETY_BOX_HALF_H, Z_DEPTH),
        ( SAFETY_BOX_HALF_W,  SAFETY_BOX_HALF_H, Z_DEPTH),
    ]
    
    us = []
    vs = []
    for X, Y, Z in pts_3d:
        u = FOCAL_LENGTH * (X / Z) + CX
        v = FOCAL_LENGTH * (Y / Z) + CY
        us.append(u)
        vs.append(v)
        
    return min(us), min(vs), max(us), max(vs)

def check_intersection(centroids, corridor_bounds):
    if len(centroids) < 5:
        return None
    
    min_u, min_v, max_u, max_v = corridor_bounds
    
    # Directed ray formulation from observed centroid trajectory (eliminates false alarms)
    u0, v0 = centroids[-1]
    u_prev, v_prev = centroids[0]
    
    dt = len(centroids) - 1
    vx = (u0 - u_prev) / max(1, dt)
    vy = (v0 - v_prev) / max(1, dt)
    
    # Liang-Barsky directed ray clipping against 2D AABB for t >= 0
    p = [-vx, vx, -vy, vy]
    q = [u0 - min_u, max_u - u0, v0 - min_v, max_v - v0]
    
    t0 = 0.0
    t1 = float('inf')
    
    for i in range(4):
        pi = p[i]
        qi = q[i]
        if abs(pi) < 1e-9:
            if qi < 0:
                return False
        else:
            t = qi / pi
            if pi < 0:
                if t > t0:
                    t0 = t
            else:
                if t < t1:
                    t1 = t
        if t0 > t1:
            return False
            
    return True

def recvall(sock, count):
    buf = b''
    while count:
        newbuf = sock.recv(count)
        if not newbuf: return None
        buf += newbuf
        count -= len(newbuf)
    return buf

def run_edge_node():
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.connect((HOST, PORT))
        print("Connected to core_phy stream.")
    except ConnectionRefusedError:
        print("Waiting for simulator stream...")
        return

    reference_frame = None
    attitude_locked = False
    centroid_history = []
    corridor = project_corridor()
    print(f"Danger Corridor 2D Bounds: {corridor}")

    while True:
        header = recvall(client_socket, 5)
        if header is None:
            break
            
        msg_type = header[0]
        payload_len = struct.unpack('>I', header[1:5])[0]
        payload = recvall(client_socket, payload_len)
        
        if payload is None:
            break

        if msg_type == 0:
            data = json.loads(payload.decode('utf-8'))
            if data.get("status") == "LOCKED":
                attitude_locked = True
                print("Attitude lock confirmed. Waiting for reference frame...")
        elif msg_type == 1:
            if not attitude_locked:
                continue

            nparr = np.frombuffer(payload, np.uint8)
            current_frame = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
            
            if reference_frame is None:
                reference_frame = current_frame.copy()
                print("Captured static reference frame.")
                continue

            diff_frame = cv2.absdiff(reference_frame, current_frame)
            _, thresh = cv2.threshold(diff_frame, 30, 255, cv2.THRESH_BINARY)

            moments = cv2.moments(thresh)
            if moments["m00"] > 0:
                cX = int(moments["m10"] / moments["m00"])
                cY = int(moments["m01"] / moments["m00"])
                
                centroid_history.append((cX, cY))
                
                status_str = "TRACKING"
                if len(centroid_history) >= 5:
                    hit = check_intersection(centroid_history, corridor)
                    if hit:
                        status_str = "THREAT_CONFIRMED"
                    else:
                        status_str = "MISS"
                
                output = {
                    "status": status_str,
                    "centroid_x": cX,
                    "centroid_y": cY,
                    "history_length": len(centroid_history)
                }
                print(json.dumps(output))

if __name__ == "__main__":
    run_edge_node()
