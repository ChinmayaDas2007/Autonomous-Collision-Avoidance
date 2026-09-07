# Project Kessler: Autonomous Operations Intelligence Desk (AOID)
## Autonomous Onboard Space Debris Detection & Low-$\Delta v$ Collision Avoidance

Project Kessler is a distributed, hybrid ground-edge intelligence and autonomous collision avoidance system designed to protect Low Earth Orbit (LEO) satellite constellations from lethal space debris and active-active satellite conjunctions.

By coupling **Ground AI orbit determination** with **closed-loop Star Tracker edge optical tracking**, Project Kessler eliminates false alarms, drastically reduces propellant consumption, and autonomously executes optimal collision avoidance burns without ground intervention during critical observation passes.

---

## System Architecture Overview

```mermaid
sequenceDiagram
    autonumber
    participant GND as Device 1: Ground AI Node (Role 3)
    participant EDGE as Device 2: Edge Payload (Role 2)
    participant FSW as Device 3: Physics & ADCS (Role 1)
    participant UI as Device 4: Operator Dashboard (Role 4)

    Note over GND: Ingests TLEs + NOAA Space Weather<br/>Computes 2-Orbit Backstep & Umbra Check
    GND->>FSW: shared/initial_ephemeris.json (Master Ephemeris Authority)
    GND->>EDGE: TCP 5555: REFINED_CDM JSON
    activate EDGE
    EDGE-->>GND: TCP ACK: {"status": "ACK", "message": "REFINED_CDM_RECEIVED"}
    deactivate GND

    Note over EDGE: Scheduler parses obs_window<br/>FSM transitions: STANDBY -> TARGET_SCHEDULED
    EDGE->>FSW: TCP 5556: SlewCommand (SLEW_TO_TARGET)
    activate FSW
    FSW-->>EDGE: TCP ACK: {"status": "ACK", "message": "SLEW_COMMAND_ACCEPTED"}
    
    Note over FSW: mrpFeedback closed-loop slew<br/>Reaction wheel cluster aligns boresight
    FSW->>EDGE: TCP 5000: Binary Stream (msg_type=0: LOCKED)
    Note over EDGE: Mandatory ADCS settling delay (damps RW jitter)<br/>FSM transitions: SLEWING -> TRACKING
    
    loop Optical Tracking Window
        FSW->>EDGE: TCP 5000: Binary Stream (msg_type=1: JPEG Frames)
        Note over EDGE: Frame Differencing (strip stars)<br/>Extract streak centroid [X,Y] & [Vx,Vy]
    end
    deactivate FSW

    Note over EDGE: Danger Corridor: Liang-Barsky Ray-AABB<br/>Active-Active Negotiation Matrix
    EDGE->>UI: TCP 5560: ManeuverDecisionPacket JSON
    EDGE->>UI: shared/processed_frames/latest_corridor_hud.jpg (2D HUD)
    Note over EDGE: FSM: DECISION_EVALUATION -> MANEUVER_ARMED -> MISSION_COMPLETE -> STANDBY
    deactivate EDGE
```

---

## Core System Modules

### 1. `gnd_ops` — Ground AI Intelligence Desk (Master Ephemeris Authority)
- **Phase 1: SGP4 Propagation**: Propagates raw TLEs to baseline Time of Closest Approach (TCA) and generates Cartesian $(\vec{r}, \vec{v})$ state vectors in meters and m/s exported to `shared/initial_ephemeris.json`.
- **Phase 2: Multi-Orbit Backstep**: Computes the optimal observation window exactly 2 orbital revolutions prior to conjunction ($T_{\text{obs}} = \text{TCA} - 2T_{\text{orbit}} \approx \text{TCA} - 3.1\text{ hours}$), ensuring identical approach geometry and ample lead time for minimal-$\Delta v$ phasing burns.
- **Phase 3: Analytical Solar Umbra Verification**: Evaluates Sun ECI coordinates and tests for Earth shadow occultation. Emits `ABORT_VISION_USE_GROUND_RADAR` if the satellite is shadowed, conserving payload power and reaction wheel life.
- **Phase 4: Space Weather & Drag Refinement**: Ingests NOAA Penticton $F_{10.7}$ and $K_p$ indices, evaluates atmospheric drag multipliers, tightens the radar error ellipsoid, and uplinks `REFINED_CDM` over TCP:5555.

### 2. `edge_pro` — Edge Processing Payload
- **Phase 1: Ingestion & Lifecycle FSM**: 7-state deterministic FSM (`BOOT` $\to$ `STANDBY` $\to$ `TARGET_SCHEDULED` $\to$ `SLEWING` $\to$ `TRACKING` $\to$ `DECISION_EVALUATION` $\to$ `MANEUVER_ARMED` $\to$ `MISSION_COMPLETE`).
- **ADCS Jitter Mitigation**: Enforces a mandatory settling delay ($6.0\text{s}$, scaled in fast simulation) before optical sensor arming to allow reaction wheel torque ripple and structural vibrations to damp out.
- **Phase 2: OpenCV Frame Differencing**: Real-time background subtraction ($|I_k - I_{k-1}|$ and $|I_k - I_0|$) stripping static Tycho catalog stars, $2\times 2$ morphological opening, and contour moment centroid extraction.
- **Phase 3: Pinhole Danger Corridor Projection & Liang-Barsky Ray Clipping**: Projects a $1\text{ km} \times 1\text{ km} \times 0.5\text{ km}$ 3D safety volume into a 2D camera AABB and evaluates forward ray traversal ($t \ge 0$), eliminating false positive alarms for receding debris.
- **Phase 4: Active-Active Negotiation & Minimal $\Delta v$ Optimization**: Evaluates the multi-variable Responsibility Scoring Matrix $S$ based on propellant reserves, remaining mission lifetime, payload downtime, and priority. Calculates an optimal cross-track burn ($\Delta v \approx 0.35\text{ m/s}$).
- **2D Software Reality HUD**: Atomically renders visual overlay (`latest_corridor_hud.jpg`) for the operator dashboard.

### 3. `core_phy` — Spacecraft Physics & Flight Software Daemon
- **Basilisk 6-DOF Simulation**: Central Earth gravity field, optional $J_2$ spherical harmonics, and 4-wheel Honeywell HR16 reaction wheel pyramid cluster.
- **Closed-Loop Attitude Control**: 2 Hz `mrpFeedback` PD guidance law ($\vec{L}_r = -K\boldsymbol{\sigma}_{B/R} - P\boldsymbol{\omega}_{B/R}$) driving boresight pointing error asymptotically to zero.
- **24-Hour Backward Integration Cap**: Numerical back-propagation capped at $24.0\text{ hours}$ ($1440.0\text{ min}$) to eliminate Runge-Kutta 4th-order (RK4) numerical drift.
- **FSW Daemon (`fsw_server.py`)**: Standalone TCP server on port 5556 ingesting `SLEW_TO_TARGET` commands, acknowledging with `SLEW_COMMAND_ACCEPTED`, and streaming 5-byte framed Star Tracker video over port 5000.

---

## Directory Structure

```
.
├── core_phy/                          # Module 3: Flight Computer & 6-DOF Physics
│   ├── fsw_server.py                  # Standalone FSW daemon & optical video streamer
│   ├── two_body_viz.py                # Basilisk testbench, 4-RW ADCS & Vizard interface
│   ├── kessler_collision_verify.bin   # Verified conjunction binary scenario
│   ├── kessler_slew_scenario.bin      # Slewing & attitude lock binary scenario
│   ├── kessler_targeting.bin          # Threat targeting binary scenario
│   ├── runner.py                      # Simulation execution script
│   └── main.py                        # Entry point for core_phy
├── edge_pro/                          # Module 2: Edge Compute Payload
│   ├── main.py                        # Standalone edge payload service entry point
│   ├── payload_manager.py             # Autonomous lifecycle coordinator
│   ├── state_machine.py               # 7-state mission safety FSM
│   ├── scheduler.py                   # Observation scheduling execution engine
│   ├── opencv_vision.py               # Frame differencing, streak tracking & 2D HUD
│   ├── danger_corridor.py             # Pinhole projection & Liang-Barsky ray clipping
│   ├── decision_engine.py             # Active-Active scoring matrix & minimal Delta-V
│   ├── decision_publisher.py          # Dashboard telemetry client
│   ├── tcp_server.py                  # Asyncio TCP Ingest server (Port 5555)
│   ├── tcp_client.py                  # Auto-reconnecting TCP client to core_phy (5556)
│   ├── models.py                      # Strictly validated dataclass JSON contracts
│   ├── config.py                      # Centralized configuration dataclass
│   └── test_*.py                      # Unit test suites (39 tests passing)
├── gnd_ops/                           # Module 1: Ground AI Intelligence Desk
│   ├── ground_ai_node.py              # SGP4, 2-orbit backstep, umbra check & uplink
│   ├── ground_ai_node.ipynb           # Interactive Ground AI research notebook
│   ├── test_ground_ai_node.py         # Ground AI unit tests (9 tests passing)
│   └── Ground_AI_Node_PRD.md          # Functional product requirement document
├── shared/                            # Inter-subsystem artifacts
│   ├── initial_ephemeris.json         # Master ephemeris Cartesian state vectors
│   ├── cdm_packet.json                # Refined CDM packet emitted by Ground AI
│   └── processed_frames/              # 2D Software Reality HUD output
│       └── latest_corridor_hud.jpg    # Operator display overlay
├── materials/                         # Project presentation, manuals & literature
├── test_three_node_e2e.py             # Automated 3-Node End-to-End integration test
├── run_vizard_scenario.py             # Unity Vizard visualization launcher
├── walkthrough.md                     # Comprehensive technical design document
├── walkthrough_edge.md                # Edge processing pipeline deep-dive
└── README.md                          # Repository overview & deployment guide
```

---

## Multi-Device Deployment Runbook

Each subsystem binds to `0.0.0.0` and connects across physical devices via standard TCP sockets:

### Step 1: Discover Local IP Addresses
Run `ifconfig | grep inet` (macOS/Linux) or `ipconfig` (Windows):
- **Device 1 (Ground Station)**: `192.168.1.101`
- **Device 2 (Edge Payload)**: `192.168.1.102`
- **Device 3 (Flight Computer / Physics)**: `192.168.1.103`

### Step 2: Start Device 3 (Flight Computer / Physics Engine)
```bash
python3 -m core_phy.fsw_server \
  --bind-host 0.0.0.0 \
  --port 5556 \
  --stream-port 5000 \
  --ephemeris shared/initial_ephemeris.json
```

### Step 3: Start Device 2 (Edge Compute Payload)
```bash
python3 -m edge_pro.main \
  --bind-host 0.0.0.0 \
  --ingest-port 5555 \
  --core-phy-host 192.168.1.103 \
  --core-phy-port 5556 \
  --stream-host 192.168.1.103 \
  --stream-port 5000 \
  --settling-delay 6.0
```

### Step 4: Run Device 1 (Ground AI Intelligence Desk)
```bash
# Nominal Mission Execution (Uplinks REFINED_CDM over TCP:5555)
python3 -m gnd_ops.ground_ai_node \
  --once \
  --uplink \
  --uplink-host 192.168.1.102 \
  --uplink-port 5555

# Earth Umbra Shadow Demonstration (Verifies autonomous optical abort)
python3 -m gnd_ops.ground_ai_node \
  --once \
  --simulate-eclipse \
  --uplink \
  --uplink-host 192.168.1.102 \
  --uplink-port 5555
```

---

## Automated Verification & Testing (49 / 49 OK)

```bash
# 1. Run Ground AI unit test suite (9 tests)
python3 -m unittest discover -s gnd_ops -p "test_*.py" -v

# 2. Run Edge Processing Payload test suite (39 tests)
python3 -m unittest discover -s edge_pro -p "test_*.py" -v

# 3. Run Full 3-Node End-to-End Integration Test
