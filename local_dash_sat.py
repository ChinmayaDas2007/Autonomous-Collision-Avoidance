#!/usr/bin/env python3
"""
PROJECT KESSLER — Local Satellite Mission Dashboard (local_dash_sat)
===================================================================
A standalone, zero-dependency Mission Control HUD for monitoring
everything running on the local laptop:
1. Ground AI Uplink Status & Ingested CDM Telemetry
2. Core Physics (core_phy) & Edge Payload (edge_pro) Lifecycle
3. Ongoing Processing Pipeline Progress
4. Vizard 3D Simulation Readiness & One-Click Launcher
5. Multi-Stage Optical Imaging Gallery (Raw, Differenced, Corridor, HUD)
6. Autonomous Avoidance Decision & Active-Active Cost Matrix

Usage:
  python3 local_dash_sat.py
  python3 local_dash_sat.py --port 8888 --no-browser
"""

import argparse
import base64
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser

# Auto-switch to virtualenv if bsk_env exists
bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
if os.path.exists(bsk_python) and sys.executable != bsk_python:
    os.execv(bsk_python, [bsk_python] + sys.argv)

ROOT_DIR = Path(__file__).resolve().parent
SHARED_DIR = ROOT_DIR / "shared"
PROCESSED_FRAMES_DIR = SHARED_DIR / "processed_frames"
EDGE_DIR = ROOT_DIR / "edge_pro"
CORE_DIR = ROOT_DIR / "core_phy"
VIZARD_APP = "/Users/chinmaya/Desktop/Vizard.app"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [LOCAL-DASH] %(message)s")
logger = logging.getLogger("local_dash_sat")


def check_port(host: str, port: int, timeout: float = 0.15) -> bool:
    """Returns True if a TCP port is open and accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_vizard_file_info() -> dict:
    """Checks for Vizard scenario binary files."""
    candidates = [
        CORE_DIR / "kessler_slew_scenario.bin",
        ROOT_DIR / "kessler_slew_scenario.bin",
        CORE_DIR / "kessler_collision_verify.bin",
        CORE_DIR / "kessler_targeting.bin",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            stat = p.stat()
            return {
                "ready": True,
                "path": str(p),
                "filename": p.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(stat.st_mtime)),
                "app_installed": os.path.exists(VIZARD_APP),
            }
    return {
        "ready": False,
        "path": None,
        "filename": None,
        "size_mb": 0,
        "mtime": None,
        "app_installed": os.path.exists(VIZARD_APP),
    }


def get_system_state() -> dict:
    """Collects comprehensive live status from sockets, files, and processes."""
    # 1. Port checks
    port_edge_ingest = check_port("127.0.0.1", 15555)
    port_core_cmd = check_port("127.0.0.1", 15556)
    port_core_stream = check_port("127.0.0.1", 15000)
    port_ws_orbit = check_port("127.0.0.1", 8001)
    port_ws_vision = check_port("127.0.0.1", 8002)

    # 2. Files
    cdm_path = SHARED_DIR / "cdm_packet.json"
    ephem_path = SHARED_DIR / "initial_ephemeris.json"
    decision_path = SHARED_DIR / "maneuver_decision.json"
    hud_path = PROCESSED_FRAMES_DIR / "latest_corridor_hud.jpg"

    cdm_data = {}
    if cdm_path.exists():
        try:
            cdm_data = json.loads(cdm_path.read_text())
            cdm_data["_file_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cdm_path.stat().st_mtime))
        except Exception:
            pass

    ephem_data = {}
    if ephem_path.exists():
        try:
            ephem_data = json.loads(ephem_path.read_text())
            ephem_data["_file_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ephem_path.stat().st_mtime))
        except Exception:
            pass

    decision_data = {}
    if decision_path.exists():
        try:
            decision_data = json.loads(decision_path.read_text())
            decision_data["_file_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(decision_path.stat().st_mtime))
        except Exception:
            pass

    # 3. Images presence
    images = {
        "raw": (EDGE_DIR / "captures" / "raw_startracker_frame.png").exists() or (CORE_DIR / "captures" / "star_tracker_optical_capture.png").exists(),
        "processed": (EDGE_DIR / "captures" / "processed_differenced_frame.png").exists(),
        "corridor": (EDGE_DIR / "captures" / "danger_corridor_tracking_overlay.png").exists(),
        "hud": hud_path.exists(),
    }

    # 4. Determine pipeline phase
    pipeline_stage = "IDLE"
    if decision_data.get("decision"):
        pipeline_stage = "DECISION_COMPLETE"
    elif images["hud"]:
        pipeline_stage = "CORRIDOR_EVALUATED"
    elif images["processed"] or port_core_stream:
        pipeline_stage = "OPTICAL_TRACKING"
    elif cdm_data:
        pipeline_stage = "CDM_INGESTED"
    elif port_edge_ingest:
        pipeline_stage = "STANDBY"

    # 5. Vizard
    vizard_info = get_vizard_file_info()

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "network": {
            "edge_ingest_15555": port_edge_ingest,
            "core_cmd_15556": port_core_cmd,
            "core_stream_15000": port_core_stream,
            "ws_orbit_8001": port_ws_orbit,
            "ws_vision_8002": port_ws_vision,
            "ground_link_active": bool(cdm_data),
        },
        "pipeline_stage": pipeline_stage,
        "cdm": cdm_data,
        "ephemeris": ephem_data,
        "decision": decision_data,
        "vizard": vizard_info,
        "images": images,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>PROJECT KESSLER — Local Satellite Mission HUD</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #070a12;
      --card-bg: rgba(16, 23, 38, 0.85);
      --border: rgba(0, 240, 255, 0.2);
      --border-bright: #00f0ff;
      --text: #e2e8f0;
      --text-dim: #94a3b8;
      --cyan: #00f0ff;
      --green: #00ff88;
      --amber: #ffb700;
      --red: #ff3b5c;
      --purple: #a855f7;
      --font-mono: 'SF Mono', Monaco, Menlo, Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      min-height: 100vh;
      padding: 16px;
      line-height: 1.5;
      background-image: 
        radial-gradient(ellipse 80% 50% at 50% -20%, rgba(0, 240, 255, 0.12), transparent),
        radial-gradient(circle at 100% 100%, rgba(168, 85, 247, 0.08), transparent);
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 20px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 16px;
      backdrop-filter: blur(10px);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand h1 {
      font-size: 1.25rem;
      letter-spacing: 2px;
      color: var(--cyan);
      text-transform: uppercase;
    }
    .badge {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 1px;
      font-weight: 700;
    }
    .badge-green { background: rgba(0, 255, 136, 0.15); color: var(--green); border: 1px solid var(--green); }
    .badge-red { background: rgba(255, 59, 92, 0.15); color: var(--red); border: 1px solid var(--red); }
    .badge-cyan { background: rgba(0, 240, 255, 0.15); color: var(--cyan); border: 1px solid var(--cyan); }
    .badge-amber { background: rgba(255, 183, 0, 0.15); color: var(--amber); border: 1px solid var(--amber); }

    .grid-top {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      backdrop-filter: blur(8px);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }
    .card-title {
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: var(--text-dim);
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      padding-bottom: 8px;
    }
    .card-title span { color: var(--cyan); }

    /* Links & Metrics */
    .link-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 6px 0;
      font-size: 0.85rem;
      border-bottom: 1px solid rgba(255,255,255,0.03);
    }
    .dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 6px;
    }
    .dot-online { background: var(--green); box-shadow: 0 0 8px var(--green); }
    .dot-offline { background: var(--red); }

    /* Pipeline Stepper */
    .pipeline-stepper {
      display: flex;
      gap: 8px;
      margin-bottom: 16px;
      overflow-x: auto;
    }
    .step-box {
      flex: 1;
      min-width: 180px;
      padding: 12px;
      border-radius: 6px;
      background: rgba(16, 23, 38, 0.6);
      border: 1px solid rgba(255,255,255,0.08);
      position: relative;
    }
    .step-box.active {
      border-color: var(--cyan);
      background: rgba(0, 240, 255, 0.08);
      box-shadow: 0 0 12px rgba(0, 240, 255, 0.2);
    }
    .step-box.done {
      border-color: var(--green);
      background: rgba(0, 255, 136, 0.08);
    }
    .step-num {
      font-family: var(--font-mono);
      font-size: 0.7rem;
      color: var(--text-dim);
    }
    .step-name {
      font-size: 0.85rem;
      font-weight: 600;
      color: #fff;
      margin-top: 4px;
    }

    /* Big Decision Banner */
    .decision-banner {
      padding: 20px;
      border-radius: 8px;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
      background: linear-gradient(135deg, rgba(16, 23, 38, 0.95), rgba(255, 59, 92, 0.15));
      border: 1px solid var(--red);
      box-shadow: 0 0 25px rgba(255, 59, 92, 0.2);
    }
    .decision-banner.no-burn {
      background: linear-gradient(135deg, rgba(16, 23, 38, 0.95), rgba(0, 255, 136, 0.12));
      border-color: var(--green);
      box-shadow: 0 0 25px rgba(0, 255, 136, 0.2);
    }
    .verdict-tag {
      font-size: 2rem;
      font-weight: 800;
      letter-spacing: 2px;
      font-family: var(--font-mono);
    }

    /* Images Gallery */
    .image-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }
    .image-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .image-header {
      padding: 8px 12px;
      font-size: 0.8rem;
      font-family: var(--font-mono);
      color: var(--cyan);
      background: rgba(0, 0, 0, 0.3);
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
    }
    .image-container {
      position: relative;
      width: 100%;
      padding-top: 75%;
      background: #000;
    }
    .image-container img {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
      cursor: pointer;
    }
    .image-caption {
      padding: 8px 12px;
      font-size: 0.75rem;
      color: var(--text-dim);
      background: rgba(0, 0, 0, 0.2);
    }

    /* Vizard Section */
    .vizard-card {
      background: linear-gradient(135deg, rgba(16, 23, 38, 0.9), rgba(168, 85, 247, 0.15));
      border: 1px solid rgba(168, 85, 247, 0.4);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .btn {
      padding: 8px 16px;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      font-weight: 600;
      border-radius: 6px;
      border: 1px solid var(--cyan);
      background: rgba(0, 240, 255, 0.15);
      color: var(--cyan);
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn:hover {
      background: var(--cyan);
      color: #000;
      box-shadow: 0 0 12px var(--cyan);
    }
    .btn-purple {
      border-color: var(--purple);
      background: rgba(168, 85, 247, 0.2);
      color: #e9d5ff;
    }
    .btn-purple:hover {
      background: var(--purple);
      color: #fff;
      box-shadow: 0 0 12px var(--purple);
    }

    /* Metrics Grid */
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 8px;
      margin: 10px 0;
    }
    .metric-cell {
      background: rgba(0,0,0,0.3);
      padding: 8px;
      border-radius: 4px;
      border: 1px solid rgba(255,255,255,0.05);
    }
    .metric-label {
      font-size: 0.7rem;
      color: var(--text-dim);
      text-transform: uppercase;
      font-family: var(--font-mono);
    }
    .metric-val {
      font-size: 1.1rem;
      font-weight: 700;
      color: #fff;
      font-family: var(--font-mono);
      margin-top: 2px;
    }

    /* Modal for Fullscreen Image */
    #modal {
      display: none;
      position: fixed;
      z-index: 1000;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(0,0,0,0.9);
      justify-content: center;
      align-items: center;
      cursor: pointer;
    }
    #modal img {
      max-width: 90vw;
      max-height: 90vh;
      border: 1px solid var(--cyan);
      box-shadow: 0 0 30px rgba(0,240,255,0.3);
    }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <span class="dot dot-online"></span>
      <h1>PROJECT KESSLER</h1>
      <span class="badge badge-cyan">LOCAL SAT HUD (core_phy + edge_pro)</span>
    </div>
    <div style="display: flex; gap: 10px; align-items: center;">
      <span id="tca-countdown" class="badge badge-amber">TCA: --:--:--</span>
      <span id="live-indicator" class="badge badge-green">LIVE AUTO-SYNC</span>
    </div>
  </header>

  <!-- PIPELINE PROGRESS STEPPER -->
  <div class="pipeline-stepper" id="stepper">
    <div class="step-box" id="step-1">
      <div class="step-num">STAGE 01</div>
      <div class="step-name">Ground CDM Ingestion</div>
      <div class="step-num" id="step-1-sub">Awaiting Link</div>
    </div>
    <div class="step-box" id="step-2">
      <div class="step-num">STAGE 02</div>
      <div class="step-name">ADCS Slew & Settling</div>
      <div class="step-num" id="step-2-sub">4-RW Reaction Control</div>
    </div>
    <div class="step-box" id="step-3">
      <div class="step-num">STAGE 03</div>
      <div class="step-name">Star Tracker Differencing</div>
      <div class="step-num" id="step-3-sub">OpenCV Streak Extraction</div>
    </div>
    <div class="step-box" id="step-4">
      <div class="step-num">STAGE 04</div>
      <div class="step-name">Danger Corridor Ray-Clipping</div>
      <div class="step-num" id="step-4-sub">Liang-Barsky 3D AABB</div>
    </div>
    <div class="step-box" id="step-5">
      <div class="step-num">STAGE 05</div>
      <div class="step-name">Autonomous Burn Decision</div>
      <div class="step-num" id="step-5-sub">Delta-V Optimization</div>
    </div>
  </div>

  <!-- DECISION VERDICT BANNER -->
  <div class="decision-banner" id="decision-banner">
    <div>
      <div style="font-size: 0.8rem; letter-spacing: 1.5px; color: var(--text-dim); text-transform: uppercase;">
        Phase 4 Autonomous Decision Verdict
      </div>
      <div class="verdict-tag" id="verdict-text" style="color: var(--red);">EXECUTE_BURN</div>
      <div style="font-size: 0.9rem; color: #fff; margin-top: 4px;" id="decision-rationale">
        Evaluating threat corridor...
      </div>
    </div>
    <div style="display: flex; gap: 24px; text-align: right;">
      <div>
        <div class="metric-label">STATUS</div>
        <div class="metric-val" id="verdict-status" style="color: var(--green);">GO</div>
      </div>
      <div>
        <div class="metric-label">DELTA-V BURN</div>
        <div class="metric-val" id="verdict-dv" style="color: var(--cyan);">0.350 m/s</div>
      </div>
      <div>
        <div class="metric-label">RESPONSIBILITY</div>
        <div class="metric-val" id="verdict-resp">SELF (100%)</div>
      </div>
    </div>
  </div>

  <!-- 3D VIZARD SIMULATION STATUS CARD -->
  <div class="vizard-card">
    <div>
      <div style="display: flex; align-items: center; gap: 10px;">
        <span class="badge badge-purple" id="vizard-badge">VIZARD SIMULATION READY</span>
        <span style="font-size: 0.85rem; color: var(--text-dim);" id="vizard-file-text">core_phy/kessler_slew_scenario.bin (1.16 MB)</span>
      </div>
      <div style="font-size: 0.85rem; color: #e2e8f0; margin-top: 6px;">
        3D Basilisk flight dynamics & 6-DOF reaction wheel attitude trajectory binary stream.
      </div>
    </div>
    <div style="display: flex; gap: 10px;">
      <button class="btn btn-purple" onclick="openVizardApp()">🚀 OPEN IN VIZARD.APP</button>
      <button class="btn" onclick="generateVizard()">🔄 RE-GENERATE 3D SCENARIO</button>
    </div>
  </div>

  <!-- MAIN TELEMETRY GRID -->
  <div class="grid-top">
    
    <!-- CARD 1: CONNECTIONS & UPLINK -->
    <div class="card">
      <div class="card-title">
        Ground Link & Local Subsystems
        <span id="ground-status-badge" class="badge badge-green">ONLINE</span>
      </div>
      <div class="link-row">
        <span><span class="dot" id="dot-ground"></span>Ground AI Ingestion (TCP 15555)</span>
        <strong id="val-ground-ingest">LISTENING</strong>
      </div>
      <div class="link-row">
        <span><span class="dot" id="dot-core-cmd"></span>Core Physics FSW Server (TCP 15556)</span>
        <strong id="val-core-cmd">ACTIVE</strong>
      </div>
      <div class="link-row">
        <span><span class="dot" id="dot-core-stream"></span>Star Tracker Optical Stream (TCP 15000)</span>
        <strong id="val-core-stream">ACTIVE</strong>
      </div>
      <div class="link-row">
        <span><span class="dot" id="dot-ws-orbit"></span>Orbit Telemetry WS (Port 8001)</span>
        <strong id="val-ws-orbit">ONLINE</strong>
      </div>
      <div class="link-row">
        <span><span class="dot" id="dot-ws-vision"></span>Vision & Decision WS (Port 8002)</span>
        <strong id="val-ws-vision">ONLINE</strong>
      </div>
      <div class="link-row" style="margin-top: 8px;">
        <span style="color: var(--text-dim);">Last Ground Ingest Sync:</span>
        <span id="val-sync-time" style="font-family: var(--font-mono); font-size: 0.8rem;">--</span>
      </div>
    </div>

    <!-- CARD 2: GROUND TELEMETRY (REFINED CDM) -->
    <div class="card">
      <div class="card-title">
        Ground AI Refined CDM Telemetry
        <span id="cdm-action-badge" class="badge badge-cyan">OPTICAL VERIFY</span>
      </div>
      <div class="metric-grid">
        <div class="metric-cell">
          <div class="metric-label">Target Asset</div>
          <div class="metric-val" id="cdm-target" style="font-size: 0.95rem;">Debris_Obj_8492</div>
        </div>
        <div class="metric-cell">
          <div class="metric-label">Miss Distance</div>
          <div class="metric-val" id="cdm-miss" style="color: var(--red);">150 m</div>
        </div>
        <div class="metric-cell">
          <div class="metric-label">Solar Flux F10.7</div>
          <div class="metric-val" id="cdm-f107">110.0 sfu</div>
        </div>
        <div class="metric-cell">
          <div class="metric-label">Drag Mult</div>
          <div class="metric-val" id="cdm-drag" style="color: var(--amber);">0.7067×</div>
        </div>
      </div>
      <div class="link-row">
        <span style="color: var(--text-dim);">Time of Closest Approach (TCA):</span>
        <span id="cdm-tca" style="font-family: var(--font-mono); font-size: 0.8rem;">--</span>
      </div>
      <div class="link-row">
        <span style="color: var(--text-dim);">Observation Window Start:</span>
        <span id="cdm-obs" style="font-family: var(--font-mono); font-size: 0.8rem;">--</span>
      </div>
      <div class="link-row">
        <span style="color: var(--text-dim);">3D Covariance Ellipsoid:</span>
        <span id="cdm-cov" style="font-family: var(--font-mono); font-size: 0.8rem;">[35.3, 106.0, 35.3] m</span>
      </div>
    </div>

    <!-- CARD 3: COST SCORE BREAKDOWN -->
    <div class="card">
      <div class="card-title">
        Active-Active Negotiation Scores
        <span class="badge badge-green">LOCAL WON</span>
      </div>
      <div class="metric-grid">
        <div class="metric-cell">
          <div class="metric-label">Local Asset</div>
          <div class="metric-val" id="score-asset" style="font-size: 0.95rem;">KesslerSat-1</div>
        </div>
        <div class="metric-cell">
          <div class="metric-label">Composite Cost S</div>
          <div class="metric-val" id="score-val" style="color: var(--green);">0.4400</div>
        </div>
      </div>
      <div style="font-size: 0.75rem; color: var(--text-dim); margin-top: 6px;">
        <div class="link-row">
          <span>Delta-V Fuel Cost (w=0.35):</span>
          <strong id="score-fuel">0.20</strong>
        </div>
        <div class="link-row">
          <span>Lifetime Impact (w=0.25):</span>
          <strong id="score-life">0.55</strong>
        </div>
        <div class="link-row">
          <span>Mission Priority (w=0.25):</span>
          <strong id="score-prio">0.75</strong>
        </div>
        <div class="link-row">
          <span>Downtime Latency (w=0.15):</span>
          <strong id="score-down">0.30</strong>
        </div>
      </div>
    </div>

  </div>

  <!-- SECTION: OPTICAL IMAGING GALLERY -->
  <div style="margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
    <h2 style="font-size: 1rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--cyan);">
      Multi-Stage Optical Vision Intelligence
    </h2>
    <span style="font-size: 0.75rem; color: var(--text-dim);">Click any frame to inspect in high-resolution</span>
  </div>

  <div class="image-grid">
    
    <!-- 1. RAW STAR TRACKER -->
    <div class="image-card">
      <div class="image-header">
        <span>01 / RAW STAR TRACKER</span>
        <span>1024×1024 OPTICAL</span>
      </div>
      <div class="image-container">
        <img id="img-raw" src="/image/raw" alt="Raw Star Tracker Capture" onclick="expandImage(this.src)">
      </div>
      <div class="image-caption">
        Raw boresight optical frame captured from satellite star-tracker during slew lock.
      </div>
    </div>

    <!-- 2. PROCESSED DIFFERENCED -->
    <div class="image-card">
      <div class="image-header">
        <span>02 / OPENCV DIFFERENCED</span>
        <span>BACKGROUND SUBTRACTED</span>
      </div>
      <div class="image-container">
        <img id="img-processed" src="/image/processed" alt="Differenced Streak Frame" onclick="expandImage(this.src)">
      </div>
      <div class="image-caption">
        Background stellar noise stripped; moving transient debris streak isolated.
      </div>
    </div>

    <!-- 3. DANGER CORRIDOR OVERLAY -->
    <div class="image-card">
      <div class="image-header">
        <span>03 / CORRIDOR PROJECTION</span>
        <span>LIANG-BARSKY RAY</span>
      </div>
      <div class="image-container">
        <img id="img-corridor" src="/image/corridor" alt="Danger Corridor Overlay" onclick="expandImage(this.src)">
      </div>
      <div class="image-caption">
        3D conical safety corridor projected into 2D camera coordinates with velocity vector.
      </div>
    </div>

    <!-- 4. FINAL HUD OVERLAY -->
    <div class="image-card" style="border-color: var(--cyan);">
      <div class="image-header" style="background: rgba(0, 240, 255, 0.15);">
        <span style="color: #fff; font-weight: 700;">04 / SOFTWARE REALITY HUD</span>
        <span style="color: var(--green);">LIVE VERIFICATION</span>
      </div>
      <div class="image-container">
        <img id="img-hud" src="/image/hud" alt="Final Corridor HUD" onclick="expandImage(this.src)">
      </div>
      <div class="image-caption">
        Final autonomous verification HUD overlay with target crosshairs, SNR metric, and burn tag.
      </div>
    </div>

  </div>

  <!-- MODAL FOR FULLSCREEN IMAGE -->
  <div id="modal" onclick="this.style.display='none'">
    <img id="modal-img" src="" alt="Enlarged View">
  </div>

  <script>
    function expandImage(src) {
      const modal = document.getElementById('modal');
      const img = document.getElementById('modal-img');
      img.src = src;
      modal.style.display = 'flex';
    }

    async function openVizardApp() {
      try {
        const res = await fetch('/api/open-vizard', { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Opening Vizard.app...');
      } catch (e) {
        alert('Failed to launch Vizard: ' + e);
      }
    }

    async function generateVizard() {
      try {
        alert('Generating fresh 3D Vizard scenario in background... please wait.');
        const res = await fetch('/api/generate-vizard', { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Vizard scenario generated!');
        fetchStatus();
      } catch (e) {
        alert('Failed to trigger Vizard generation: ' + e);
      }
    }

    // Refresh Telemetry & Status
    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // 1. Network / Sockets
        const net = data.network;
        setDot('dot-ground', net.edge_ingest_15555);
        setDot('dot-core-cmd', net.core_cmd_15556);
        setDot('dot-core-stream', net.core_stream_15000);
        setDot('dot-ws-orbit', net.ws_orbit_8001);
        setDot('dot-ws-vision', net.ws_vision_8002);

        document.getElementById('val-ground-ingest').textContent = net.edge_ingest_15555 ? 'ONLINE (15555)' : 'OFFLINE';
        document.getElementById('val-core-cmd').textContent = net.core_cmd_15556 ? 'ONLINE (15556)' : 'OFFLINE';
        document.getElementById('val-core-stream').textContent = net.core_stream_15000 ? 'ONLINE (15000)' : 'OFFLINE';
        document.getElementById('val-ws-orbit').textContent = net.ws_orbit_8001 ? 'BROADCASTING' : 'OFFLINE';
        document.getElementById('val-ws-vision').textContent = net.ws_vision_8002 ? 'BROADCASTING' : 'OFFLINE';

        const gBadge = document.getElementById('ground-status-badge');
        if (data.cdm && Object.keys(data.cdm).length > 0) {
          gBadge.className = 'badge badge-green';
          gBadge.textContent = 'CDM INGESTED';
          document.getElementById('val-sync-time').textContent = data.cdm._file_mtime || data.cdm.header?.timestamp_utc || '--';
        } else {
          gBadge.className = 'badge badge-amber';
          gBadge.textContent = 'AWAITING UPLINK';
        }

        // 2. Stepper
        updateStepper(data.pipeline_stage);

        // 3. Ground CDM
        if (data.cdm?.conjunction_data) {
          const conj = data.cdm.conjunction_data;
          document.getElementById('cdm-target').textContent = conj.secondary_asset || '--';
          document.getElementById('cdm-miss').textContent = conj.miss_distance_km ? (conj.miss_distance_km * 1000).toFixed(0) + ' m' : '--';
          document.getElementById('cdm-tca').textContent = conj.time_of_closest_approach || '--';
          document.getElementById('cdm-obs').textContent = conj.observation_window_start_utc || '--';
          
          if (conj.time_of_closest_approach) {
            updateCountdown(conj.time_of_closest_approach);
          }
        }
        if (data.cdm?.ai_drag_prediction) {
          const drag = data.cdm.ai_drag_prediction;
          document.getElementById('cdm-f107').textContent = (drag.f107_flux || 0).toFixed(1) + ' sfu';
          document.getElementById('cdm-drag').textContent = (drag.drag_multiplier || 1.0).toFixed(4) + '×';
          if (drag.ellipsoid_covariance_matrix) {
            document.getElementById('cdm-cov').textContent = JSON.stringify(drag.ellipsoid_covariance_matrix);
          }
        }

        // 4. Decision Banner
        const dec = data.decision;
        const banner = document.getElementById('decision-banner');
        const vText = document.getElementById('verdict-text');
        const vStatus = document.getElementById('verdict-status');
        const vDv = document.getElementById('verdict-dv');
        const vResp = document.getElementById('verdict-resp');
        const vRat = document.getElementById('decision-rationale');

        if (dec && dec.decision) {
          vText.textContent = dec.decision;
          vStatus.textContent = dec.decision_status || 'GO';
          vDv.textContent = (dec.delta_v_magnitude_mps || 0).toFixed(3) + ' m/s';
          vResp.textContent = (dec.responsibility || 'SELF').toUpperCase() + (dec.responsibility === 'self' ? ' (100%)' : '');
          vRat.textContent = dec.rationale || 'Autonomous avoidance burn authorized.';

          if (dec.decision === 'EXECUTE_BURN') {
            banner.className = 'decision-banner';
            vText.style.color = 'var(--red)';
          } else {
            banner.className = 'decision-banner no-burn';
            vText.style.color = 'var(--green)';
          }

          // Scores
          const scores = dec.scoring_audit || {};
          const localFactors = scores.local_factors || {};
          document.getElementById('score-asset').textContent = scores.local_asset || 'KesslerSat-1';
          document.getElementById('score-val').textContent = (scores.local_score !== undefined) ? scores.local_score.toFixed(4) : '--';
          document.getElementById('score-fuel').textContent = (localFactors.cost_delta_v !== undefined) ? localFactors.cost_delta_v.toFixed(2) : '--';
          document.getElementById('score-life').textContent = (localFactors.cost_lifetime !== undefined) ? localFactors.cost_lifetime.toFixed(2) : '--';
          document.getElementById('score-prio').textContent = (localFactors.cost_priority !== undefined) ? localFactors.cost_priority.toFixed(2) : '--';
          document.getElementById('score-down').textContent = (localFactors.cost_downtime !== undefined) ? localFactors.cost_downtime.toFixed(2) : '--';
        }

        // 5. Vizard
        const viz = data.vizard;
        const vizBadge = document.getElementById('vizard-badge');
        const vizText = document.getElementById('vizard-file-text');
        if (viz.ready) {
          vizBadge.className = 'badge badge-green';
          vizBadge.textContent = 'VIZARD 3D SIMULATION READY';
          vizText.textContent = viz.filename + ' (' + viz.size_mb + ' MB) — Updated: ' + viz.mtime;
        } else {
          vizBadge.className = 'badge badge-amber';
          vizBadge.textContent = 'SIMULATION NOT READY';
          vizText.textContent = 'Scenario binary not yet generated.';
        }

      } catch (e) {
        console.error('Fetch error:', e);
      }
    }

    function setDot(id, online) {
      const el = document.getElementById(id);
      if (el) el.className = online ? 'dot dot-online' : 'dot dot-offline';
    }

    function updateStepper(stage) {
      const order = ['STANDBY', 'CDM_INGESTED', 'OPTICAL_TRACKING', 'CORRIDOR_EVALUATED', 'DECISION_COMPLETE'];
      const curIdx = order.indexOf(stage);

      for (let i = 1; i <= 5; i++) {
        const box = document.getElementById('step-' + i);
        if (i - 1 < curIdx) {
          box.className = 'step-box done';
        } else if (i - 1 === curIdx) {
          box.className = 'step-box active';
        } else {
          box.className = 'step-box';
        }
      }
    }

    function updateCountdown(tcaIso) {
      try {
        const tca = new Date(tcaIso).getTime();
        const now = Date.now();
        const diff = Math.floor((tca - now) / 1000);
        if (diff > 0) {
          const h = String(Math.floor(diff / 3600)).padStart(2, '0');
          const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
          const s = String(diff % 60).padStart(2, '0');
          document.getElementById('tca-countdown').textContent = 'TCA: -' + h + ':' + m + ':' + s;
        } else {
          document.getElementById('tca-countdown').textContent = 'TCA REACHED';
        }
      } catch (e) {}
    }

    // Auto-refresh every 1500ms
    fetchStatus();
    setInterval(fetchStatus, 1500);

    // Refresh images every 3000ms
    setInterval(() => {
      const t = '?t=' + Date.now();
      document.getElementById('img-raw').src = '/image/raw' + t;
      document.getElementById('img-processed').src = '/image/processed' + t;
      document.getElementById('img-corridor').src = '/image/corridor' + t;
      document.getElementById('img-hud').src = '/image/hud' + t;
    }, 3000);
  </script>
</body>
</html>
"""


class SatDashboardHandler(BaseHTTPRequestHandler):
    """Custom HTTP Request Handler for Local Satellite Dashboard."""

    def log_message(self, format, *args):
        # Silence routine access logs to keep terminal clean
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

        elif path == "/api/status":
            state = get_system_state()
            payload = json.dumps(state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        elif path.startswith("/image/"):
            img_type = path.split("/")[2]
            target_file = None
            if img_type == "raw":
                cand = [EDGE_DIR / "captures" / "raw_startracker_frame.png", CORE_DIR / "captures" / "star_tracker_optical_capture.png"]
                target_file = next((p for p in cand if p.exists()), None)
            elif img_type == "processed":
                target_file = EDGE_DIR / "captures" / "processed_differenced_frame.png"
            elif img_type == "corridor":
                target_file = EDGE_DIR / "captures" / "danger_corridor_tracking_overlay.png"
            elif img_type == "hud":
                target_file = PROCESSED_FRAMES_DIR / "latest_corridor_hud.jpg"

            if target_file and target_file.exists():
                mime = "image/jpeg" if target_file.suffix.lower() in [".jpg", ".jpeg"] else "image/png"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                with open(target_file, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/open-vizard":
            vizard_info = get_vizard_file_info()
            if not vizard_info["ready"]:
                self._send_json({"success": False, "message": "No Vizard scenario .bin file found."})
                return

            bin_path = vizard_info["path"]
            if not os.path.exists(VIZARD_APP):
                self._send_json({"success": False, "message": f"Vizard.app not found at {VIZARD_APP}"})
                return

            try:
                subprocess.Popen(["open", "-a", VIZARD_APP, bin_path])
                self._send_json({"success": True, "message": f"Launched Vizard.app with {Path(bin_path).name}"})
            except Exception as e:
                self._send_json({"success": False, "message": f"Error opening Vizard: {e}"})

        elif path == "/api/generate-vizard":
            script = ROOT_DIR / "run_vizard_scenario.py"
            if not script.exists():
                self._send_json({"success": False, "message": "run_vizard_scenario.py not found."})
                return

            def _run():
                subprocess.run([bsk_python, str(script)], cwd=str(ROOT_DIR))

            threading.Thread(target=_run, daemon=True).start()
            self._send_json({"success": True, "message": "Vizard scenario generation started in background."})
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def print_cli_summary(state: dict, port: int):
    """Prints a beautiful ANSI telemetry dashboard summary to stdout."""
    net = state["network"]
    cdm = state.get("cdm", {}).get("conjunction_data", {})
    dec = state.get("decision", {})
    viz = state.get("vizard", {})

    print("\n" + "═" * 70)
    print(" 🛰️  PROJECT KESSLER — LOCAL SATELLITE MISSION DASHBOARD")
    print(f"    Web HUD URL: http://localhost:{port}")
    print("═" * 70)
    print(" [1] SUBSYSTEM & NETWORK HEALTH:")
    print(f"     • Ground AI Ingestion (TCP 15555): {'[ONLINE]' if net['edge_ingest_15555'] else '[OFFLINE]'}")
    print(f"     • Core FSW Command    (TCP 15556): {'[ONLINE]' if net['core_cmd_15556'] else '[OFFLINE]'}")
    print(f"     • Optical Video Stream (TCP 15000): {'[ONLINE]' if net['core_stream_15000'] else '[OFFLINE]'}")
    print(f"     • Orbit WS Broadcaster (TCP  8001): {'[ONLINE]' if net['ws_orbit_8001'] else '[OFFLINE]'}")
    print(f"     • Vision WS Broadcaster(TCP  8002): {'[ONLINE]' if net['ws_vision_8002'] else '[OFFLINE]'}")
    print("─" * 70)
    print(" [2] GROUND INGESTED TELEMETRY (CDM):")
    if cdm:
        print(f"     • Target Asset:     {cdm.get('secondary_asset', 'N/A')}")
        print(f"     • Miss Distance:    {cdm.get('miss_distance_km', 0) * 1000.0:.1f} m")
        print(f"     • TCA (UTC):        {cdm.get('time_of_closest_approach', 'N/A')}")
        print(f"     • Observation T_0:  {cdm.get('observation_window_start_utc', 'N/A')}")
    else:
        print("     • [No Ground CDM ingested yet — Awaiting uplink from Laptop 3]")
    print("─" * 70)
    print(" [3] 3D VIZARD SIMULATION ENGINE:")
    if viz.get("ready"):
        print(f"     • Status: [READY] — {viz.get('filename')} ({viz.get('size_mb')} MB)")
        print(f"     • Path:   {viz.get('path')}")
        print("     • Click 'OPEN IN VIZARD.APP' in Web HUD to view 3D orbit simulation.")
    else:
        print("     • Status: [NOT READY] (Scenario binary not generated yet)")
    print("─" * 70)
    print(" [4] AUTONOMOUS MANEUVER DECISION (PHASE 4):")
    if dec:
        print(f"     • Decision:         {dec.get('decision')} ({dec.get('decision_status')})")
        print(f"     • Delta-V Burn:     {dec.get('delta_v_magnitude_mps', 0.0):.3f} m/s")
        print(f"     • Burn Vector:      {dec.get('delta_v_vector_mps')}")
        print(f"     • Responsibility:   {dec.get('responsibility', 'self').upper()}")
        print(f"     • Rationale:        {dec.get('rationale')}")
    else:
        print("     • [No Maneuver Decision recorded yet]")
    print("═" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Project Kessler — Local Satellite Mission HUD")
    parser.add_argument("--port", type=int, default=8888, help="Port to bind dashboard web server (default: 8888)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    args = parser.parse_args()

    state = get_system_state()
    print_cli_summary(state, args.port)

    server = HTTPServer(("0.0.0.0", args.port), SatDashboardHandler)
    logger.info("Local Satellite Dashboard active at http://0.0.0.0:%d", args.port)
    logger.info("Access locally via http://localhost:%d", args.port)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Dashboard server terminated by user.")
        server.server_close()


if __name__ == "__main__":
    main()
