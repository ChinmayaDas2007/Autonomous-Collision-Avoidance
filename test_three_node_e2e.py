"""
Project Kessler — 3-Node End-to-End Integration Verification Test
==================================================================
Tests simultaneous interaction across all three distributed subsystems:
  Node 1: Ground AI (gnd_ops) — Master Ephemeris Authority & Refined CDM Uplink
  Node 2: Edge Compute Payload (edge_pro) — ADCS Settling, Vision Differencing, Corridor Clipping, Decision Engine
  Node 3: Spacecraft Physics & FSW (core_phy) — Slew Command Server, Video Streaming Daemon
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
import unittest

from core_phy.fsw_server import FSWPhysicsServer
from edge_pro.config import PayloadConfig
from edge_pro.payload_manager import PayloadManager
from edge_pro.state_machine import PayloadState
from gnd_ops.ground_ai_node import GroundAINode, EphemerisProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [E2E] %(message)s")
logger = logging.getLogger("E2E")


class TestThreeNodeIntegration(unittest.IsolatedAsyncioTestCase):
    """Full 3-Node End-to-End Integration Test Suite."""

    async def asyncSetUp(self):
        self.cmd_port = 15556
        self.stream_port = 15000
        self.ingest_port = 15555
        self.hud_dir = Path("shared/processed_frames")
        self.hud_dir.mkdir(parents=True, exist_ok=True)
        self.hud_file = self.hud_dir / "latest_corridor_hud.jpg"

        # 1. Boot Node 3: core_phy FSW & Optical Stream Server
        logger.info("[Setup] Launching Node 3 (core_phy FSWPhysicsServer)...")
        self.fsw_server = FSWPhysicsServer(
            bind_host="127.0.0.1",
            cmd_port=self.cmd_port,
            stream_port=self.stream_port,
            ephemeris_path="shared/initial_ephemeris.json",
        )
        self.fsw_server.start()
        time.sleep(0.5)

        # 2. Boot Node 2: edge_pro Payload Manager
        logger.info("[Setup] Launching Node 2 (edge_pro PayloadManager)...")
        self.payload_config = PayloadConfig(
            ingest_host="127.0.0.1",
            ingest_port=self.ingest_port,
            core_phy_host="127.0.0.1",
            core_phy_port=self.cmd_port,
            stream_host="127.0.0.1",
            stream_port=self.stream_port,
            adcs_settling_delay_seconds=1.5,
            tracking_duration_seconds=2.5,
            immediate_dispatch=True,
            processed_frames_dir=self.hud_dir,
            log_level="INFO",
        )
        self.payload_manager = PayloadManager(config=self.payload_config)
        await self.payload_manager.start()
        self.assertEqual(self.payload_manager.fsm.current_state, PayloadState.STANDBY)
        time.sleep(0.5)

    async def asyncTearDown(self):
        logger.info("[Teardown] Stopping Node 2 (edge_pro)...")
        await self.payload_manager.stop()
        logger.info("[Teardown] Stopping Node 3 (core_phy)...")
        self.fsw_server.stop()
        time.sleep(0.2)

    async def test_full_three_node_pipeline_execution(self):
        """
        Executes complete mission lifecycle:
        1. Ground AI generates Single Source of Truth ephemeris & Refined CDM.
        2. Ground AI uplinks Refined CDM via TCP to Edge Payload.
        3. Edge Payload dispatches SlewCommand to core_phy FSW server.
        4. Edge Payload awaits ADCS settling delay (1.5s scaled).
        5. Edge Payload streams Star Tracker optical frames from core_phy:15000.
        6. Phase 2 frame differencing detects moving streak centroid and velocity vector.
        7. Phase 3 evaluates Danger Corridor AABB intersection via Liang-Barsky algorithm.
        8. Phase 4 computes Active-Active negotiation and minimal Delta-V burn vector.
        9. Phase 4 exports 2D Software Reality HUD frame (latest_corridor_hud.jpg).
        """
        logger.info("=== Starting 3-Node End-to-End Mission Pipeline ===")

        # Step 1: Execute Node 1 (Ground AI) with TCP uplink to Node 2
        logger.info("[Node 1] Executing Ground AI run_once with TCP uplink...")
        ground_node = GroundAINode()
        conjunction = {
            "primary_asset": "Kessler_Sat_1",
            "secondary_asset": "Debris_Obj_8492",
            "time_of_closest_approach": "2026-09-08T14:32:15Z",
            "miss_distance_km": 0.15,
        }
        ground_node.run_once(
            conjunction=conjunction,
            out_path=Path("shared/cdm_packet.json"),
            uplink=True,
            uplink_host="127.0.0.1",
            uplink_port=self.ingest_port,
        )

        # Verify Master Ephemeris file was generated
        ephem_file = Path("shared/initial_ephemeris.json")
        self.assertTrue(ephem_file.exists(), "Master ephemeris file shared/initial_ephemeris.json missing!")
        ephem_data = json.loads(ephem_file.read_text())
        self.assertEqual(ephem_data["header"]["authority"], "Ground_AI_Node")
        self.assertIn("r_eci_m", ephem_data["primary_asset"])
        self.assertIn("v_eci_m_s", ephem_data["debris_asset"])
        logger.info("[Node 1] Ephemeris verified: Primary r=%s", ephem_data["primary_asset"]["r_eci_m"])

        # Verify Refined CDM file was generated
        cdm_file = Path("shared/cdm_packet.json")
        self.assertTrue(cdm_file.exists(), "Refined CDM file shared/cdm_packet.json missing!")
        cdm_data = json.loads(cdm_file.read_text())
        self.assertEqual(cdm_data["header"]["type"], "REFINED_CDM")
        self.assertIn("observation_window_start_utc", cdm_data["conjunction_data"])
        logger.info("[Node 1] Refined CDM verified: Action=%s", cdm_data.get("action"))

        # Step 2: Await Node 2 (Edge Compute Payload) mission lifecycle completion
        logger.info("[Node 2] Monitoring Edge Payload lifecycle execution...")
        max_wait_seconds = 15.0
        start_t = time.time()
        completed = False

        while time.time() - start_t < max_wait_seconds:
            # Check if mission has progressed through all states to completion or standby
            if self.payload_manager.latest_decision is not None:
                completed = True
                break
            await asyncio.sleep(0.3)

        self.assertTrue(
            completed,
            f"Edge payload did not complete decision evaluation within {max_wait_seconds}s. "
            f"Current state: {self.payload_manager.fsm.current_state}"
        )

        decision = self.payload_manager.latest_decision
        logger.info("[Node 2 Decision Result]")
        logger.info("  Decision:     %s", decision.decision)
        logger.info("  Status:       %s", decision.decision_status)
        logger.info("  Target Asset: %s", decision.target_asset)
        logger.info("  Delta-V Vector (m/s): %s", decision.delta_v_vector_mps)
        logger.info("  Delta-V Magnitude (m/s): %s", decision.delta_v_magnitude_mps)

        self.assertIn(decision.decision, ["EXECUTE_BURN", "NO_MANEUVER_REQUIRED", "MONITOR_PEER_MANEUVER"])
        self.assertIn(decision.decision_status, ["GO", "NO_GO"])

        # Step 3: Verify 2D Software Reality HUD frame export
        logger.info("[Verification] Checking 2D Software Reality HUD output...")
        self.assertTrue(self.hud_file.exists(), f"HUD frame {self.hud_file} was not generated!")
        self.assertGreater(self.hud_file.stat().st_size, 1000, "HUD frame file is empty or corrupted!")
        logger.info("[Verification] HUD frame verified: %s (%d bytes)", self.hud_file, self.hud_file.stat().st_size)

        logger.info("=== 3-Node End-to-End Integration Test SUCCESSFUL ===")


if __name__ == "__main__":
    unittest.main()
