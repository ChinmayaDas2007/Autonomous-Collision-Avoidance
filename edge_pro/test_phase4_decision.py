"""
Project Kessler - Edge Processing Payload
Phase 4 Unit Tests: Go/No-Go Decision Engine & Active-Active Avoidance Protocol
-------------------------------------------------------------------------------
Tests:
1. Contract validation for SpacecraftCapability, CorridorAssessment, ManeuverDecisionPacket.
2. Scoring function calculation, cost normalizations, and tie-breaking.
3. Burn Authorization: Corridor miss yields NO_MANEUVER_REQUIRED (fuel conserved).
4. Passive Debris encounter: active spacecraft assumes 100% burn responsibility.
5. Active-Active Spacecraft vs Spacecraft negotiation (Defense vs Commercial, Fuel reserves, EOL).
6. State machine transitions (DECISION_EVALUATION -> MANEUVER_ARMED -> MISSION_COMPLETE).
7. End-to-end mission loop with TCP Dashboard telemetry broadcast.
"""

import asyncio
from datetime import datetime, timezone
import json
import socket
import unittest

from edge_pro.config import PayloadConfig
from edge_pro.corridor_interface import MockCorridorEvaluator
from edge_pro.decision_engine import (
    ActiveActiveDecisionEngine,
    PRIORITY_COST_MAP,
)
from edge_pro.decision_publisher import TCPDecisionPublisher
from edge_pro.models import (
    CDMHeader,
    ConjunctionData,
    CorridorAssessment,
    ManeuverDecisionPacket,
    PacketValidationError,
    RefinedCDM,
    SpacecraftCapability,
)
from edge_pro.payload_manager import PayloadManager
from edge_pro.scheduler import format_iso8601_utc
from edge_pro.state_machine import PayloadState, PayloadStateMachine, StateTransitionError


class TestPhase4Contracts(unittest.TestCase):
    """Tests schema validation and serialization for Phase 4 packets."""

    def test_spacecraft_capability_validation(self):
        valid = SpacecraftCapability(
            asset_id="Sat-Alpha",
            remaining_delta_v_mps=100.0,
            remaining_lifetime_years=5.0,
            mission_priority="PRIMARY_SCIENCE",
            recovery_latency_sec=120.0,
        )
        valid.validate()

        with self.assertRaises(PacketValidationError):
            SpacecraftCapability(
                asset_id="",
                remaining_delta_v_mps=100.0,
                remaining_lifetime_years=5.0,
                mission_priority="COMMERCIAL",
                recovery_latency_sec=120.0,
            ).validate()

        with self.assertRaises(PacketValidationError):
            SpacecraftCapability(
                asset_id="Sat-Beta",
                remaining_delta_v_mps=-10.0,
                remaining_lifetime_years=5.0,
                mission_priority="COMMERCIAL",
                recovery_latency_sec=120.0,
            ).validate()

    def test_corridor_assessment_validation(self):
        valid = CorridorAssessment(
            target_asset="Debris_Obj_8492",
            corridor_intersected=True,
            time_to_closest_approach_sec=3600.0,
            miss_distance_m=250.0,
            threat_vector_normalized=[-0.6, 0.0, 0.8],
            confidence_score=0.95,
        )
        valid.validate()

        with self.assertRaises(PacketValidationError):
            CorridorAssessment(
                target_asset="Debris_Obj_8492",
                corridor_intersected=True,
                time_to_closest_approach_sec=3600.0,
                miss_distance_m=250.0,
                confidence_score=1.5,  # Invalid confidence > 1.0
            ).validate()

    def test_maneuver_decision_packet_serialization(self):
        now_str = format_iso8601_utc(datetime.now(timezone.utc))
        packet = ManeuverDecisionPacket(
            decision="EXECUTE_BURN",
            decision_status="GO",
            responsibility="self",
            corridor_breached=True,
            target_asset="Debris_Obj_8492",
            timestamp_utc=now_str,
            delta_v_vector_mps=[0.0, 0.35, 0.0],
            delta_v_magnitude_mps=0.35,
            burn_epoch_utc=now_str,
            rationale="Collision confirmed",
        )
        packet.validate()

        # Wire serialization
        wire_data = packet.to_wire()
        self.assertTrue(wire_data.endswith(b"\n"))

        # Deserialization
        reconstructed = ManeuverDecisionPacket.from_json(wire_data.decode("utf-8"))
        self.assertEqual(reconstructed.decision, "EXECUTE_BURN")
        self.assertEqual(reconstructed.decision_status, "GO")
        self.assertEqual(reconstructed.delta_v_magnitude_mps, 0.35)


class TestScoringMatrixAndCalculations(unittest.TestCase):
    """Tests the multi-variable scoring function S and cost factor normalizations."""

    def setUp(self):
        self.config = PayloadConfig(
            own_asset_id="Sat-Self",
            own_remaining_delta_v_mps=120.0,
            own_remaining_lifetime_years=6.0,
            own_mission_priority="PRIMARY_SCIENCE",
            own_recovery_latency_sec=180.0,
            weight_lifetime=0.25,
            weight_delta_v=0.35,
            weight_priority=0.25,
            weight_downtime=0.15,
        )
        self.engine = ActiveActiveDecisionEngine(config=self.config)

    def test_cost_factor_bounds_and_directionality(self):
        # Asset with maximum resources
        abundant_sat = SpacecraftCapability(
            asset_id="AbundantSat",
            remaining_delta_v_mps=150.0,
            remaining_lifetime_years=10.0,
            mission_priority="SECONDARY",  # Low priority -> low cost
            recovery_latency_sec=0.0,
        )
        costs = self.engine.calculate_cost_factors(abundant_sat)
        self.assertEqual(costs["cost_delta_v"], 0.0)
        self.assertEqual(costs["cost_lifetime"], 0.0)
        self.assertEqual(costs["cost_priority"], 0.10)
        self.assertEqual(costs["cost_downtime"], 0.0)

        # Asset with depleted resources
        depleted_sat = SpacecraftCapability(
            asset_id="DepletedSat",
            remaining_delta_v_mps=0.0,
            remaining_lifetime_years=0.0,
            mission_priority="NATIONAL_DEFENSE",  # High priority -> high cost
            recovery_latency_sec=600.0,
        )
        costs_dep = self.engine.calculate_cost_factors(depleted_sat)
        self.assertEqual(costs_dep["cost_delta_v"], 1.0)
        self.assertEqual(costs_dep["cost_lifetime"], 1.0)
        self.assertEqual(costs_dep["cost_priority"], 1.0)
        self.assertEqual(costs_dep["cost_downtime"], 1.0)

    def test_minimal_delta_v_orthogonality(self):
        corridor = CorridorAssessment(
            target_asset="Debris_Test",
            corridor_intersected=True,
            time_to_closest_approach_sec=4000.0,
            miss_distance_m=100.0,
            threat_vector_normalized=[1.0, 0.0, 0.0],
        )
        dv_vec, mag = self.engine.compute_minimal_avoidance_delta_v(corridor)
        self.assertAlmostEqual(mag, 0.35, places=3)
        # Dot product with threat vector [1, 0, 0] must be zero (perpendicular)
        dot_product = dv_vec[0] * 1.0 + dv_vec[1] * 0.0 + dv_vec[2] * 0.0
        self.assertAlmostEqual(dot_product, 0.0, places=4)


class TestGoNoGoAndNegotiation(unittest.TestCase):
    """Tests the Go/No-Go authorization logic and Active-Active negotiation outcomes."""

    def setUp(self):
        self.config = PayloadConfig(
            own_asset_id="KesslerSat-1",
            own_remaining_delta_v_mps=120.0,
            own_remaining_lifetime_years=5.0,
            own_mission_priority="PRIMARY_SCIENCE",
            own_recovery_latency_sec=180.0,
        )
        self.engine = ActiveActiveDecisionEngine(config=self.config)

        self.sample_cdm = RefinedCDM(
            header=CDMHeader(
                type="REFINED_CDM",
                timestamp_utc="2026-09-08T12:00:00Z",
            ),
            conjunction_data=ConjunctionData(
                primary_asset="KesslerSat-1",
                secondary_asset="Debris_Obj_8492",
                time_of_closest_approach="2026-09-08T14:32:00Z",
                observation_window_start_utc="2026-09-08T13:00:00Z",
                miss_distance_km=0.35,
            ),
        )

    def test_burn_authorization_miss_corridor(self):
        """Case 1: Debris streak trajectory misses the corridor -> NO_MANEUVER_REQUIRED."""
        corridor_clear = CorridorAssessment(
            target_asset="Debris_Obj_8492",
            corridor_intersected=False,  # Clear!
            time_to_closest_approach_sec=5000.0,
            miss_distance_m=2200.0,
        )
        decision = self.engine.evaluate_decision(self.sample_cdm, corridor_clear)
        self.assertEqual(decision.decision, "NO_MANEUVER_REQUIRED")
        self.assertEqual(decision.decision_status, "NO_GO")
        self.assertEqual(decision.responsibility, "NONE")
        self.assertFalse(decision.corridor_breached)
        self.assertIsNone(decision.delta_v_vector_mps)

    def test_burn_authorization_passive_debris_hit(self):
        """Case 2: Debris streak enters safety corridor -> EXECUTE_BURN assigned to self."""
        corridor_hit = CorridorAssessment(
            target_asset="Debris_Obj_8492",
            corridor_intersected=True,  # Hit!
            time_to_closest_approach_sec=5000.0,
            miss_distance_m=250.0,
            threat_vector_normalized=[-0.7071, 0.0, 0.7071],
        )
        # Peer is None (passive debris)
        decision = self.engine.evaluate_decision(self.sample_cdm, corridor_hit, peer_capability=None)
        self.assertEqual(decision.decision, "EXECUTE_BURN")
        self.assertEqual(decision.decision_status, "GO")
        self.assertEqual(decision.responsibility, "self")
        self.assertTrue(decision.corridor_breached)
        self.assertIsNotNone(decision.delta_v_vector_mps)
        self.assertEqual(len(decision.delta_v_vector_mps), 3)
        self.assertGreater(decision.delta_v_magnitude_mps, 0.0)
        self.assertIsNotNone(decision.burn_epoch_utc)

    def test_active_active_defense_vs_commercial(self):
        """
        Case 3: Active-Active conjunction.
        Self is High Priority (Defense). Peer is Commercial.
        Peer must assume maneuver responsibility.
        """
        corridor_hit = CorridorAssessment(
            target_asset="CommercialSat-9",
            corridor_intersected=True,
            time_to_closest_approach_sec=5000.0,
            miss_distance_m=150.0,
        )

        # Set self to Defense
        self.engine.own_capability.mission_priority = "NATIONAL_DEFENSE"

        peer = SpacecraftCapability(
            asset_id="CommercialSat-9",
            remaining_delta_v_mps=120.0,
            remaining_lifetime_years=5.0,
            mission_priority="COMMERCIAL",
            recovery_latency_sec=180.0,
            is_maneuverable=True,
        )

        decision = self.engine.evaluate_decision(self.sample_cdm, corridor_hit, peer_capability=peer)
        self.assertEqual(decision.decision, "MONITOR_PEER_MANEUVER")
        self.assertEqual(decision.decision_status, "NO_GO")
        self.assertEqual(decision.responsibility, "CommercialSat-9")
        self.assertIn("CommercialSat-9", decision.rationale)

    def test_active_active_high_fuel_vs_low_fuel(self):
        """
        Case 4: Active-Active conjunction.
        Self has 140 m/s fuel reserves. Peer is running on empty with 10 m/s.
        Self must assume maneuver responsibility.
        """
        corridor_hit = CorridorAssessment(
            target_asset="DepletedSat-2",
            corridor_intersected=True,
            time_to_closest_approach_sec=5000.0,
            miss_distance_m=120.0,
        )

        self.engine.own_capability.remaining_delta_v_mps = 140.0
        self.engine.own_capability.mission_priority = "PRIMARY_SCIENCE"

        peer = SpacecraftCapability(
            asset_id="DepletedSat-2",
            remaining_delta_v_mps=10.0,  # Depleted!
            remaining_lifetime_years=5.0,
            mission_priority="PRIMARY_SCIENCE",
            recovery_latency_sec=180.0,
            is_maneuverable=True,
        )

        decision = self.engine.evaluate_decision(self.sample_cdm, corridor_hit, peer_capability=peer)
        self.assertEqual(decision.decision, "EXECUTE_BURN")
        self.assertEqual(decision.decision_status, "GO")
        self.assertEqual(decision.responsibility, "self")


class TestFSMPhase4Transitions(unittest.TestCase):
    """Tests the state machine lifecycle incorporating Phase 4 states."""

    def test_full_phase4_lifecycle(self):
        fsm = PayloadStateMachine()
        fsm.transition_to(PayloadState.STANDBY, "Boot ok")
        fsm.transition_to(PayloadState.TARGET_SCHEDULED, "CDM scheduled")
        fsm.transition_to(PayloadState.SLEWING, "T0 reached")
        fsm.transition_to(PayloadState.TRACKING, "Slew locked")
        fsm.transition_to(PayloadState.DECISION_EVALUATION, "OpticalDifferencing Complete")
        fsm.transition_to(PayloadState.MANEUVER_ARMED, "Burn authorized")
        fsm.transition_to(PayloadState.MISSION_COMPLETE, "Burn dispatched")
        fsm.transition_to(PayloadState.STANDBY, "Cycle reset")
        self.assertEqual(fsm.current_state, PayloadState.STANDBY)

    def test_corridor_clear_bypasses_maneuver_armed(self):
        fsm = PayloadStateMachine()
        fsm.transition_to(PayloadState.STANDBY)
        fsm.transition_to(PayloadState.TARGET_SCHEDULED)
        fsm.transition_to(PayloadState.SLEWING)
        fsm.transition_to(PayloadState.TRACKING)
        fsm.transition_to(PayloadState.DECISION_EVALUATION)
        # Direct transition to MISSION_COMPLETE when NO_MANEUVER_REQUIRED
        fsm.transition_to(PayloadState.MISSION_COMPLETE, "Threat cleared corridor")
        self.assertEqual(fsm.current_state, PayloadState.MISSION_COMPLETE)


class TestEndToEndPhase4WithDashboard(unittest.IsolatedAsyncioTestCase):
    """Verifies end-to-end mission loop and dashboard socket broadcast."""

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def test_full_pipeline_with_dashboard_broadcast(self):
        ingest_port = self._find_free_port()
        core_phy_port = self._find_free_port()
        dashboard_port = self._find_free_port()

        config = PayloadConfig(
            ingest_host="127.0.0.1",
            ingest_port=ingest_port,
            core_phy_host="127.0.0.1",
            core_phy_port=core_phy_port,
            dashboard_host="127.0.0.1",
            dashboard_port=dashboard_port,
            immediate_dispatch=True,
            time_scale_factor=100.0,
            tracking_duration_seconds=0.1,
        )

        # Mock core_phy server
        slew_cmds_received = []

        async def handle_core_phy(reader, writer):
            while True:
                line = await reader.readline()
                if not line:
                    break
                slew_cmds_received.append(json.loads(line.decode("utf-8")))

        core_phy_server = await asyncio.start_server(handle_core_phy, "127.0.0.1", core_phy_port)

        # Mock dashboard server
        dashboard_packets_received = []

        async def handle_dashboard(reader, writer):
            while True:
                line = await reader.readline()
                if not line:
                    break
                dashboard_packets_received.append(json.loads(line.decode("utf-8")))

        dashboard_server = await asyncio.start_server(handle_dashboard, "127.0.0.1", dashboard_port)

        # Start PayloadManager with Phase 3 & 4
        corridor_mock = MockCorridorEvaluator(force_intersection=True, simulated_miss_distance_m=200.0)
        manager = PayloadManager(config=config, corridor_evaluator=corridor_mock)
        await manager.start()

        # Ingest Ground CDM
        now_dt = datetime.now(timezone.utc)
        cdm_payload = {
            "header": {
                "type": "REFINED_CDM",
                "timestamp_utc": format_iso8601_utc(now_dt),
            },
            "conjunction_data": {
                "primary_asset": "KesslerSat-1",
                "secondary_asset": "Threat_Debris_X",
                "time_of_closest_approach": format_iso8601_utc(now_dt),
                "observation_window_start_utc": format_iso8601_utc(now_dt),
                "miss_distance_km": 0.2,
            },
        }

        # Transmit CDM to ingest socket
        r, w = await asyncio.open_connection("127.0.0.1", ingest_port)
        w.write((json.dumps(cdm_payload) + "\n").encode("utf-8"))
        await w.drain()
        w.close()
        await w.wait_closed()

        # Wait for pipeline to complete
        await asyncio.sleep(0.5)

        # Assertions
        self.assertEqual(len(slew_cmds_received), 1)
        self.assertEqual(slew_cmds_received[0]["target_asset"], "Threat_Debris_X")

        self.assertEqual(len(dashboard_packets_received), 1)
        dash_pkt = dashboard_packets_received[0]
        self.assertEqual(dash_pkt["type"], "MANEUVER_DECISION")
        self.assertEqual(dash_pkt["decision"], "EXECUTE_BURN")
        self.assertEqual(dash_pkt["decision_status"], "GO")
        self.assertEqual(dash_pkt["responsibility"], "self")
        self.assertGreater(dash_pkt["delta_v_magnitude_mps"], 0.0)

        # Cleanup
        await manager.stop()
        core_phy_server.close()
        await core_phy_server.wait_closed()
        dashboard_server.close()
        await dashboard_server.wait_closed()


if __name__ == "__main__":
    unittest.main()
