"""
Project Kessler — Ground AI Node Unit Tests
-------------------------------------------------------------------------------
Tests EphemerisProcessor (Phases 1-3), CDMBuilder (Phase 4 Contract),
analytical solar ephemeris, NOAA ingestion fallbacks, and TCP uplink to edge_pro.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from gnd_ops.ground_ai_node import (
    CONFIG,
    GroundAINode,
    EphemerisProcessor,
    DataIngestor,
    FeatureBuilder,
    DragPredictor,
    CDMBuilder,
    Publisher,
    calculate_sun_position_eci,
)
from edge_pro.models import RefinedCDM, PacketValidationError
from edge_pro.tcp_server import TCPIngestServer


class TestGroundAINode(unittest.TestCase):
    """Test suite for Ground AI intelligence desk."""

    def setUp(self):
        self.config = dict(CONFIG)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config["shared_dir"] = Path(self.temp_dir.name)
        self.config["cdm_output_path"] = Path(self.temp_dir.name) / "cdm_packet.json"
        self.tca = datetime(2026, 9, 8, 14, 32, 15, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    # ── Analytical Solar Ephemeris ─────────────────────────────────────────────
    def test_analytical_solar_ephemeris(self):
        """Verifies analytical Sun ECI coordinates and unit vector norm."""
        dt = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)  # Summer solstice
        sun_pos, sun_hat = calculate_sun_position_eci(dt)

        # Distance should be near 1 AU (~1.496e8 km)
        dist = math.sqrt(sum(c**2 for c in sun_pos))
        self.assertAlmostEqual(dist / 1.496e8, 1.0, delta=0.05)

        # Unit vector norm must be 1.0
        norm = math.sqrt(sum(c**2 for c in sun_hat))
        self.assertAlmostEqual(norm, 1.0, places=5)

    # ── Phase 1: SGP4 Propagation ──────────────────────────────────────────────
    def test_sgp4_propagation_phase1(self):
        """Tests Phase 1 TLE propagation to TCA using SGP4."""
        processor = EphemerisProcessor(self.config)
        res = processor.propagate_to_tca(self.tca)

        self.assertIn("primary_eci_km", res)
        self.assertIn("debris_eci_km", res)
        self.assertIn("primary_mean_motion_rad_per_min", res)

        p_r = res["primary_eci_km"]
        r_mag = math.sqrt(sum(c**2 for c in p_r))
        # LEO radius should be around 6700 - 6850 km
        self.assertGreater(r_mag, 6600.0)
        self.assertLess(r_mag, 7000.0)

        # Mean motion for ~400 km ISS orbit is around 0.067 - 0.068 rad/min
        self.assertGreater(res["primary_mean_motion_rad_per_min"], 0.06)
        self.assertLess(res["primary_mean_motion_rad_per_min"], 0.075)

    # ── Phase 2: Multi-Orbit Backstep ──────────────────────────────────────────
    def test_multi_orbit_backstep_phase2(self):
        """Tests Phase 2 Multi-Orbit Backstep obs_start = TCA - 2*T."""
        processor = EphemerisProcessor(self.config)
        n0 = 0.067648  # approx 15.5 revs/day
        res = processor.compute_observation_window(self.tca, n0)

        expected_period = round((2.0 * math.pi) / n0, 4)
        expected_backstep = round(2.0 * expected_period, 4)
        self.assertEqual(res["orbital_period_min"], expected_period)
        self.assertEqual(res["backstep_min"], expected_backstep)

        expected_obs_start = self.tca - timedelta(minutes=expected_backstep)
        self.assertEqual(res["obs_start_utc"], expected_obs_start)

    # ── Phase 3: Solar Umbra Check ────────────────────────────────────────────
    def test_umbra_check_phase3_sunlit(self):
        """Tests sunlit scenario (satellite facing sunward)."""
        processor = EphemerisProcessor(self.config)
        obs_dt = datetime(2026, 9, 8, 11, 26, 29, tzinfo=timezone.utc)
        # Position pointing roughly sunward
        _, sun_hat = calculate_sun_position_eci(obs_dt)
        sunward_sat = [c * 6771.0 for c in sun_hat]

        res = processor.check_umbra(obs_dt, sunward_sat)
        self.assertFalse(res["is_in_umbra"])
        self.assertGreater(res["proj_km"], 0.0)

    def test_umbra_check_phase3_shadow(self):
        """Tests umbra shadow scenario (satellite directly behind Earth from Sun)."""
        processor = EphemerisProcessor(self.config)
        obs_dt = datetime(2026, 9, 8, 11, 26, 29, tzinfo=timezone.utc)
        _, sun_hat = calculate_sun_position_eci(obs_dt)
        # Directly anti-sunward inside cylinder
        anti_sun_sat = [-c * 6771.0 for c in sun_hat]

        res = processor.check_umbra(obs_dt, anti_sun_sat)
        self.assertTrue(res["is_in_umbra"])
        self.assertLess(res["proj_km"], 0.0)
        self.assertLess(res["perp_dist_km"], self.config["earth_radius_km"])

    # ── Phase 4: Output Contract & edge_pro Validation ─────────────────────────
    def test_refined_cdm_contract_validation_phase4(self):
        """Verifies generated packet strictly complies with edge_pro.models.RefinedCDM."""
        node = GroundAINode(self.config)
        conjunction = {
            "primary_asset": "Kessler_Sat_1",
            "secondary_asset": "Debris_Obj_8492",
            "time_of_closest_approach": self.tca.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "miss_distance_km": 0.15,
        }
        pkt = node.run_once(conjunction)

        # Parse with edge_pro.models.RefinedCDM
        refined = RefinedCDM.from_dict(pkt)
        self.assertEqual(refined.header.type, "REFINED_CDM")
        self.assertEqual(refined.conjunction_data.primary_asset, "Kessler_Sat_1")
        self.assertEqual(refined.conjunction_data.secondary_asset, "Debris_Obj_8492")
        self.assertIsNotNone(refined.conjunction_data.observation_window_start_utc)
        self.assertIsNotNone(refined.ai_drag_prediction)
        self.assertEqual(len(refined.ai_drag_prediction.ellipsoid_covariance_matrix), 3)

        # Also verify from JSON string
        json_str = json.dumps(pkt)
        refined_from_json = RefinedCDM.from_json(json_str)
        self.assertEqual(refined_from_json.action, refined.action)

    # ── Eclipse Abort Safety Override ──────────────────────────────────────────
    def test_eclipse_abort_action(self):
        """Verifies simulate_eclipse triggers ABORT_VISION_USE_GROUND_RADAR."""
        node = GroundAINode(self.config)
        conjunction = {
            "primary_asset": "Kessler_Sat_1",
            "secondary_asset": "Debris_Obj_8492",
            "time_of_closest_approach": self.tca.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "miss_distance_km": 0.15,
        }
        pkt = node.run_once(conjunction, simulate_eclipse=True)

        self.assertEqual(pkt["action"], "ABORT_VISION_USE_GROUND_RADAR")
        self.assertTrue(pkt["_audit"]["is_in_umbra"])

    # ── Space Weather Fallback Handling ───────────────────────────────────────
    def test_data_ingestion_fallbacks(self):
        """Tests that DataIngestor gracefully defaults when network endpoints fail."""
        bad_config = dict(self.config)
        bad_config["noaa_f107_url"] = "http://127.0.0.1:59999/nonexistent"
        bad_config["noaa_kp_url"] = "http://127.0.0.1:59999/nonexistent"
        bad_config["http_timeout_sec"] = 0.5

        ingestor = DataIngestor(bad_config)
        res = ingestor.poll()

        self.assertEqual(res["f107"], self.config["default_f107"])
        self.assertEqual(res["kp"], self.config["default_kp"])
        self.assertTrue(res["stale_data"])

    # ── TCP Uplink to edge_pro TCPIngestServer ─────────────────────────────────
    def test_tcp_uplink_to_edge_ingest_server(self):
        """Verifies Ground AI Publisher connects to edge_pro TCPIngestServer and receives ACK."""
        received_packets = []

        async def run_uplink_test():
            async def on_cdm(cdm: RefinedCDM):
                received_packets.append(cdm)

            server = TCPIngestServer(
                host="127.0.0.1",
                port=5678,  # Dedicated test port
                on_cdm_callback=on_cdm,
            )
            await server.start()

            publisher = Publisher(self.config)
            test_packet = {
                "header": {
                    "type": "REFINED_CDM",
                    "timestamp_utc": "2026-09-08T12:00:00Z",
                },
                "conjunction_data": {
                    "primary_asset": "Kessler_Sat_1",
                    "secondary_asset": "Debris_Obj_8492",
                    "time_of_closest_approach": "2026-09-08T14:32:15Z",
                    "observation_window_start_utc": "2026-09-08T11:26:29Z",
                    "miss_distance_km": 0.12,
                },
                "ai_drag_prediction": {
                    "f107_flux": 150.0,
                    "kp_index": 3.0,
                    "drag_multiplier": 1.0,
                    "ellipsoid_covariance_matrix": [50.0, 150.0, 50.0],
                },
                "action": "RECOMMEND_OPTICAL_CONFIRMATION",
            }

            # Uplink via TCP in worker thread so event loop remains unblocked
            success = await asyncio.to_thread(
                publisher.uplink_to_edge,
                test_packet,
                host="127.0.0.1",
                port=5678,
                timeout=2.0,
            )
            await asyncio.sleep(0.1)
            await server.stop()
            return success

        success = asyncio.run(run_uplink_test())
        self.assertTrue(success)
        self.assertEqual(len(received_packets), 1)
        self.assertEqual(received_packets[0].conjunction_data.secondary_asset, "Debris_Obj_8492")


if __name__ == "__main__":
    unittest.main()
