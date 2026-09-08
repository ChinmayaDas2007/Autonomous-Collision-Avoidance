# Project Kessler — Edge Processing Payload (`edge_pro`)
## Phase 1: Mission Management & Slew Targeting

Autonomous orbital collision avoidance edge compute software package.

---

## 1. Overview & Operational Architecture

The `edge_pro` package implements the onboard edge computing node for Project Kessler (Problem Statement 5: Autonomous Operations Intelligence Desk). It executes Phase 1 of the mission pipeline:

1. **Non-Blocking TCP Ingestion:** Binds a local TCP socket (`127.0.0.1:5555`) to receive Refined Conjunction Data Messages (CDMs) from Ground AI.
2. **UTC Timestamp Mathematics & Scheduling:** Parses ISO 8601 timestamps and computes the critical observation window:
   $$T_0 = \text{TCA} - 30\text{ minutes}$$
3. **Finite State Machine (FSM):** Enforces deterministic state transitions (`BOOT` ➔ `STANDBY` ➔ `TARGET_SCHEDULED` ➔ `SLEWING` ➔ `TRACKING` ➔ `MISSION_COMPLETE`).
4. **TCP Slew Command Publisher:** Transmits the targeting command to the Basilisk physics engine node (`core_phy`) at $T_0$ (or immediately in hackathon time-loop mode).
5. **Phase 2 Vision Hook:** Provides an abstract interface for seamless downstream integration with OpenCV frame differencing and streak centroid extraction.

```
Ground AI (NOAA ML) ──[TCP:5555]──▶ TCPIngestServer ──▶ ObservationScheduler ──▶ TCPSlewPublisher ──[TCP:5556]──▶ core_phy (Basilisk)
                                           │                     │
                                           ▼                     ▼
                                    PayloadStateMachine    VisionPipelineInterface (Phase 2)
```

---

## 2. JSON Wire Contracts

### Ingestion Contract (Expected from Ground AI):
```json
{
  "header": {
    "type": "REFINED_CDM",
    "timestamp_utc": "2026-09-08T12:00:00Z"
  },
  "conjunction_data": {
    "primary_asset": "Kessler_Sat_1",
    "secondary_asset": "Debris_Obj_8492",
    "time_of_closest_approach": "2026-09-08T14:32:15Z"
  }
}
```

### Publisher Contract (Dispatched to `core_phy`):
```json
{
  "command": "SLEW_TO_TARGET",
  "target_asset": "Debris_Obj_8492",
  "execute_at_utc": "2026-09-08T14:02:15Z",
  "sensor_mode": "OPTICAL_TRACKING"
}
```

Framing over TCP is newline-delimited (`\n`), matching standard aerospace ground/payload communication streams.

---

## 3. Directory Layout

```
edge_pro/
├── __init__.py            # Public package API exports
├── config.py              # Centralized configuration dataclass
├── models.py              # Strictly typed JSON schemas & validators
├── scheduler.py           # ISO 8601 UTC time math (T_0 = TCA - 30m) & timers
├── state_machine.py       # Deterministic Payload FSM with safety invariants
├── tcp_server.py          # Asynchronous TCP ingestion server (Ground AI)
├── tcp_client.py          # Asynchronous TCP publisher client (core_phy)
├── vision_interface.py    # Modular Phase 2 OpenCV differencing interface
├── payload_manager.py     # Master orchestrator and CLI entry point
├── test_payload.py        # 12 automated unit & loopback integration tests
├── mock_ground_ai.py      # Ground AI simulation node for testing/demo
├── mock_core_phy.py       # Basilisk core_phy receiver node for testing/demo
└── README.md              # Technical documentation
```

---

## 4. Running the Code

### A. Run the Standalone Slew Maneuver & Optical Capture Simulator
Executes a simulated end-to-end mission: ADCS closed-loop slew maneuver with terminal progress telemetry, star tracker sensor exposure, pure-Python 512x512 PNG capture, ASCII viewfinder, and Phase 2 detection JSON output:

```bash
# Animated real-time slew maneuver & capture
./edge_pro/simulate_slew_and_capture.py

# Or fast-forward mode (instant slew convergence)
./edge_pro/simulate_slew_and_capture.py --fast
```

The captured optical frame is written to:
`edge_pro/captures/debris_optical_frame_20260908T140215Z.png`

### B. Run Automated Unit & Loopback Test Suite
```bash
python3 -m unittest edge_pro/test_payload.py -v
```

### C. Run Full 3-Node End-to-End Live Socket Pipeline

**Terminal 1: Start Basilisk Physics Node (`core_phy`) Listener**
```bash
python3 -m edge_pro.mock_core_phy --port 5556
```

**Terminal 2: Launch Edge Processing Payload (`edge_pro`)**
```bash
# Hackathon mode: immediate dispatch of slew command upon ingestion
python3 -m edge_pro.payload_manager --immediate --ingest-port 5555 --core-phy-port 5556
```

**Terminal 3: Uplink Refined CDM from Ground AI**
```bash
python3 -m edge_pro.mock_ground_ai --port 5555 --tca "2026-09-08T14:32:15Z"
```

---

## 5. CLI Arguments for `payload_manager.py`

| Flag | Default | Description |
|---|---|---|
| `--ingest-host` | `127.0.0.1` | Host to bind the Ground AI TCP ingestion server |
| `--ingest-port` | `5555` | TCP port to listen for incoming Refined CDMs |
| `--core-phy-host`| `127.0.0.1` | Target host of the Basilisk physics node |
| `--core-phy-port`| `5556` | Target TCP port to publish Slew commands |
| `--lead-time` | `1800.0` | Observation lead time in seconds ($T_0 = \text{TCA} - 30\text{ min}$) |
| `--immediate` | `False` | Hackathon testing bypass (dispatches command immediately) |
| `--time-scale` | `1.0` | Accelerated simulation clock multiplier |
| `--tracking-duration` | `5.0` | Duration in seconds to maintain optical tracking |
| `--log-level` | `INFO` | Verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
