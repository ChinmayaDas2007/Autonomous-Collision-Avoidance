# Project Kessler — Complete End-to-End Edge Payload Walkthrough (Phases 1 to 4)

We have successfully audited, upgraded, and integrated all four phases of the **Project Kessler Autonomous Operations Intelligence Desk (AOID) Edge Processing Payload** inside [`edge_pro/`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro).

The pipeline connects **Ground AI Ingestion $\to$ ADCS Slew Targeting $\to$ OpenCV Frame Differencing $\to$ Liang-Barsky Danger Corridor Projection $\to$ Go/No-Go Active-Active Decision Engine $\to$ Dashboard TCP Broadcast**.

All **39 automated unit tests** pass in under 1.6 seconds.

---

## 1. Complete Multi-Phase Pipeline Architecture

```mermaid
flowchart TD
    subgraph Ground AI Node
        G[Ground AI: XGBoost / LSTM] -->|Refined CDM :5555\n(TCA, obs_window_start, drag)| INGEST
    end

    subgraph Phase 1: Mission Management
        INGEST[TCPIngestServer] --> FSM[PayloadStateMachine\nBOOT ➔ STANDBY]
        FSM --> SCHED[ObservationScheduler]
        SCHED -->|SlewCommand :5556\nSLEW_TO_TARGET| BSK[core_phy Basilisk FSW]
    end

    subgraph Phase 2: OpenCV Frame Differencing
        BSK -->|Attitude Lock Confirmed| CAM[Star Tracker Stream :5000]
        CAM --> CV[OpenCVVisionPipeline\nHybrid Frame Differencing]
        CV --> MORPH[Morphological Noise Rejection]
        MORPH --> CENT[Subpixel Centroid & Velocity\n(x, y), [Vx, Vy], Heading]
    end

    subgraph Phase 3: Danger Corridor Projection
        CENT --> CORR[PinholeCorridorEvaluator\n15° FOV, f=2430.6px]
        BOX[3D Safety Box\n1km x 1km x 0.5km] --> AABB[2D Pixel AABB\n(u_min, v_min, u_max, v_max)]
        AABB --> LB[Liang-Barsky Directed Ray Clipping\nt >= 0 (Forward-Only)]
        CENT --> LB
        LB -->|Ray Misses or Receding| MISS[CorridorAssessment:\nintersected = False]
        LB -->|Ray Penetrates Box| HIT[CorridorAssessment:\nintersected = True]
    end

    subgraph Phase 4: Decision Engine & Active-Active
        MISS --> DEC_NOGO[NO_MANEUVER_REQUIRED\nStatus: NO_GO | Fuel Saved]
        HIT --> CONJ_CHECK{Asset Type?}
        CONJ_CHECK -->|Passive Debris| SOLO_BURN[EXECUTE_BURN (Self)\nMinimal Delta-V = 0.35 m/s]
        CONJ_CHECK -->|Active Spacecraft| MATRIX[Active-Active Scoring S\nw1*Life + w2*Dv + w3*Prio + w4*Down]
        MATRIX -->|Self Lower Score| BURN_SELF[EXECUTE_BURN (Self)\nOptimal Nodal Epoch]
        MATRIX -->|Peer Lower Score| MON_PEER[MONITOR_PEER_MANEUVER (Peer)\nStatus: NO_GO]
    end

    subgraph UI Dashboard & Flight Dynamics
        DEC_NOGO --> PUB[TCPDecisionPublisher :5560]
        SOLO_BURN --> PUB
        BURN_SELF --> PUB
        MON_PEER --> PUB
        PUB --> UI[PS 5 Streamlit Dashboard]
        PUB --> PROP[core_phy Propulsion Node]
    end
```

---

## 2. Phase 3 Upgrades & Technical Details

### 1. Pinhole Projection Geometry ([`edge_pro/danger_corridor.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/danger_corridor.py))
- **Sensor Intrinsics**: $640 \times 480$ resolution, $15.0^\circ$ Field of View.
- **Focal Length**:
  $$f = \frac{W / 2}{\tan(\text{FOV} / 2)} = \frac{320}{\tan(7.5^\circ)} \approx 2430.63\text{ pixels}$$
- **3D Safety Box to 2D Bounding Box**:
  Projects the satellite's $1\text{ km} \times 1\text{ km}$ safety boundaries at stand-off observation range ($Z = 10\text{ km}$):
  $$\Delta u = f \cdot \frac{X_{\text{half}}}{Z} = 2430.63 \times \frac{500}{10000} \approx 121.5\text{ pixels}$$
  $$\text{Corridor Bounds: } [198.5, 441.5] \times [118.5, 361.5]\text{ px}$$

### 2. Liang-Barsky Directed Ray Clipping ([`liang_barsky_ray_aabb_intersect`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/danger_corridor.py#L76-L148))
Eliminates your teammate's `polyfit` infinite-line bug and non-deterministic random jitter:
- Parameterizes the forward trajectory for forward time $t \ge 0$:
  $$u(t) = u_0 + V_x \cdot t, \quad v(t) = v_0 + V_y \cdot t$$
- Solves boundary clipping planes:
  $$p_1 = -V_x, \; q_1 = u_0 - u_{\min}; \quad p_2 = V_x, \; q_2 = u_{\max} - u_0$$
  $$p_3 = -V_y, \; q_3 = v_0 - v_{\min}; \quad p_4 = V_y, \; q_4 = v_{\max} - v_0$$
- **Receding Trajectory Rejection**: If debris is moving *away* from the corridor on the collision line, $t_{\text{enter}} > t_{\text{exit}}$ or $t_{\text{exit}} < 0$, immediately returning `False` and preventing false positive burns.
- **Steep Vertical Ingress**: Handles $V_x = 0$ or $V_y = 0$ deterministically with zero division errors.

### 3. Teammate Script Patch ([`Autonomous-Collision-Avoidance-main/edge_node.py`](file:///Users/chinmaya/Desktop/Project_Kessler/Autonomous-Collision-Avoidance-main/edge_node.py))
- Upgraded `check_intersection()` in-place to use Liang-Barsky directed ray math.
- Now correctly flags Simulation 1 as `MISS` and Simulation 2 as `THREAT_CONFIRMED`.

---

## 3. Automated Test Suite Results

Ran the complete test suite across all 4 phases:

```bash
/Users/chinmaya/bsk_env/bin/python3 -m unittest discover -s edge_pro -p "test_*.py" -v
```

```text
test_eclipse_blindness_abort (test_payload.TestEndToEndLoopbackAndEclipse) ... ok
test_full_pipeline_immediate_dispatch (test_payload.TestEndToEndLoopbackAndEclipse) ... ok
test_extended_input_parsing (test_payload.TestPacketContracts) ... ok
test_invalid_header_type_rejected (test_payload.TestPacketContracts) ... ok
test_missing_observation_window_start_rejected (test_payload.TestPacketContracts) ... ok
test_slew_command_contract (test_payload.TestPacketContracts) ... ok
test_valid_input_parsing (test_payload.TestPacketContracts) ... ok
test_illegal_transition_rejection (test_payload.TestPayloadStateMachine) ... ok
test_nominal_lifecycle (test_payload.TestPayloadStateMachine) ... ok
test_observation_window_pass_through_exact (test_payload.TestTimestampAndSchedulerPassThrough) ... ok
test_timezone_normalization_to_utc (test_payload.TestTimestampAndSchedulerPassThrough) ... ok
test_moving_streak_isolated (test_phase2_vision.TestPhase2VisionPipeline) ... ok
test_noise_rejection_isolated_pixels (test_phase2_vision.TestPhase2VisionPipeline) ... ok
test_pipeline_integration_with_payload_manager (test_phase2_vision.TestPhase2VisionPipeline) ... ok
test_static_stars_eliminated (test_phase2_vision.TestPhase2VisionPipeline) ... ok
test_velocity_vector_estimation (test_phase2_vision.TestPhase2VisionPipeline) ... ok
test_hit_detections_confirmed (test_phase3_corridor.TestPinholeCorridorEvaluator) ... ok
test_miss_detections_cleared (test_phase3_corridor.TestPinholeCorridorEvaluator) ... ok
test_boresight_projection_at_optical_center (test_phase3_corridor.TestPinholeProjection) ... ok
test_focal_length_calculation (test_phase3_corridor.TestPinholeProjection) ... ok
test_safety_box_2d_corridor_bounds (test_phase3_corridor.TestPinholeProjection) ... ok
test_direct_forward_hit (test_phase3_corridor.TestLiangBarskyDirectedRayTracing) ... ok
test_origin_already_inside_box (test_phase3_corridor.TestLiangBarskyDirectedRayTracing) ... ok
test_parallel_miss (test_phase3_corridor.TestLiangBarskyDirectedRayTracing) ... ok
test_receding_trajectory_rejected (test_phase3_corridor.TestLiangBarskyDirectedRayTracing) ... ok
test_steep_vertical_penetration (test_phase3_corridor.TestLiangBarskyDirectedRayTracing) ... ok
test_teammate_check_intersection_hit_and_miss (test_phase3_corridor.TestTeammateScriptUpgrade) ... ok
test_full_pipeline_with_dashboard_broadcast (test_phase4_decision.TestEndToEndPhase4WithDashboard) ... ok
test_corridor_clear_bypasses_maneuver_armed (test_phase4_decision.TestFSMPhase4Transitions) ... ok
test_full_phase4_lifecycle (test_phase4_decision.TestFSMPhase4Transitions) ... ok
test_active_active_defense_vs_commercial (test_phase4_decision.TestGoNoGoAndNegotiation) ... ok
test_active_active_high_fuel_vs_low_fuel (test_phase4_decision.TestGoNoGoAndNegotiation) ... ok
test_burn_authorization_miss_corridor (test_phase4_decision.TestGoNoGoAndNegotiation) ... ok
test_burn_authorization_passive_debris_hit (test_phase4_decision.TestGoNoGoAndNegotiation) ... ok
test_corridor_assessment_validation (test_phase4_decision.TestPhase4Contracts) ... ok
test_maneuver_decision_packet_serialization (test_phase4_decision.TestPhase4Contracts) ... ok
test_spacecraft_capability_validation (test_phase4_decision.TestPhase4Contracts) ... ok
test_cost_factor_bounds_and_directionality (test_phase4_decision.TestScoringMatrixAndCalculations) ... ok
test_minimal_delta_v_orthogonality (test_phase4_decision.TestScoringMatrixAndCalculations) ... ok

----------------------------------------------------------------------
Ran 39 tests in 1.572s

OK
```

---

## 4. File Manifest Across All Phases

| File | Phase | Role |
| :--- | :--- | :--- |
| [`edge_pro/models.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/models.py) | 1, 3, 4 | Data contracts: `RefinedCDM`, `SlewCommand`, `SpacecraftCapability`, `CorridorAssessment`, `ManeuverDecisionPacket` |
| [`edge_pro/scheduler.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/scheduler.py) | 1 | Observation scheduling pass-through, countdown timer, UTC normalization |
| [`edge_pro/state_machine.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/state_machine.py) | 1–4 | Payload FSM (`BOOT`, `STANDBY`, `TARGET_SCHEDULED`, `SLEWING`, `TRACKING`, `DECISION_EVALUATION`, `MANEUVER_ARMED`, `MISSION_COMPLETE`) |
| [`edge_pro/tcp_server.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/tcp_server.py) | 1 | Async TCP ingestion server for Ground AI CDMs (:5555) |
| [`edge_pro/tcp_client.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/tcp_client.py) | 1 | Slew command TCP publisher to `core_phy` (:5556) |
| [`edge_pro/opencv_vision.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/opencv_vision.py) | 2 | Hybrid consecutive/reference background subtraction, contour morphological filtering, subpixel moments, velocity estimation |
| [`edge_pro/danger_corridor.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/danger_corridor.py) | 3 | Pinhole camera matrix projection, 3D-to-2D safety box AABB, Liang-Barsky directed ray clipping ($t \ge 0$) |
| [`edge_pro/decision_engine.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/decision_engine.py) | 4 | Burn authorization (`NO_MANEUVER_REQUIRED`), Active-Active multi-variable scoring matrix $S$, minimal orthogonal $\Delta v$ |
| [`edge_pro/decision_publisher.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/decision_publisher.py) | 4 | TCP decision telemetry publisher & broadcast server to UI Dashboard (:5560) |
| [`edge_pro/payload_manager.py`](file:///Users/chinmaya/Desktop/Project_Kessler/edge_pro/payload_manager.py) | 1–4 | Master asynchronous mission orchestrator |
| [`edge_node.py`](file:///Users/chinmaya/Desktop/Project_Kessler/Autonomous-Collision-Avoidance-main/edge_node.py) | 3 | Teammate's upgraded standalone demo node |
| [`mock_vizard_stream.py`](file:///Users/chinmaya/Desktop/Project_Kessler/Autonomous-Collision-Avoidance-main/mock_vizard_stream.py) | 2–3 | Synthetic Star Tracker video streaming test harness |
