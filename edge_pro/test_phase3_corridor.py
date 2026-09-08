"""
Project Kessler - Edge Processing Payload
Phase 3 Unit Tests: Danger Corridor Projection & Liang-Barsky Ray Tracing
-------------------------------------------------------------------------------
Tests:
1. Pinhole camera intrinsics and 3D safety box to 2D pixel projection.
2. Liang-Barsky directed ray clipping:
   - Direct forward hit.
   - Receding trajectory rejection (eliminates teammate's infinite-line false positive).
   - Parallel miss.
   - Steep vertical trajectory (Vx = 0, no division by zero).
   - Origin inside safety volume.
3. PinholeCorridorEvaluator interface with Phase 2 DebrisDetections.
4. Teammate standalone script check_intersection compatibility.
"""

import math
import sys
import os
import unittest

from edge_pro.danger_corridor import (
    PinholeCorridorProjector,
    PinholeCorridorEvaluator,
    liang_barsky_ray_aabb_intersect,
)
from edge_pro.vision_interface import DebrisDetection

# Import teammate's upgraded check_intersection function
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Autonomous-Collision-Avoidance-main")))
try:
    from edge_node import (
        check_intersection as teammate_check_intersection,
        project_corridor as teammate_project_corridor,
    )
except ImportError:
    teammate_check_intersection = None
    teammate_project_corridor = None


class TestPinholeProjection(unittest.TestCase):
    """Validates camera projection geometry from 3D meters to 2D pixels."""

    def setUp(self):
        self.projector = PinholeCorridorProjector(
            image_width=640,
            image_height=480,
            fov_degrees=15.0,
            safety_box_half_w_m=500.0,
            safety_box_half_h_m=500.0,
            nominal_z_depth_m=10000.0,
        )

    def test_focal_length_calculation(self):
        # f = (W/2) / tan(FOV/2) = 320 / tan(7.5 deg)
        expected_f = 320.0 / math.tan(math.radians(7.5))
        self.assertAlmostEqual(self.projector.focal_length, expected_f, places=2)

    def test_boresight_projection_at_optical_center(self):
        # A point along the exact camera boresight [0, 0, Z] must map to (cx, cy) = (320, 240)
        u, v = self.projector.project_3d_to_pixel(0.0, 0.0, 10000.0)
        self.assertAlmostEqual(u, 320.0, places=3)
        self.assertAlmostEqual(v, 240.0, places=3)

    def test_safety_box_2d_corridor_bounds(self):
        min_u, min_v, max_u, max_v = self.projector.get_corridor_2d_bounds()
        # Half width in pixels: (500 / 10000) * f = 0.05 * f
        half_span_px = 0.05 * self.projector.focal_length
        self.assertAlmostEqual(min_u, 320.0 - half_span_px, places=2)
        self.assertAlmostEqual(max_u, 320.0 + half_span_px, places=2)
        self.assertAlmostEqual(min_v, 240.0 - half_span_px, places=2)
        self.assertAlmostEqual(max_v, 240.0 + half_span_px, places=2)


class TestLiangBarskyDirectedRayTracing(unittest.TestCase):
    """Tests directed ray-to-box intersection math across all geometric edge cases."""

    def setUp(self):
        # Typical 2D corridor bounds roughly in center: [100, 500] x [100, 400]
        self.bounds = (100.0, 100.0, 500.0, 400.0)

    def test_direct_forward_hit(self):
        # Origin at (20, 20), heading towards center (300, 250) with velocity [28, 23]
        hit, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=20.0, v0=20.0, vx=28.0, vy=23.0, bounds=self.bounds
        )
        self.assertTrue(hit)
        self.assertIsNotNone(t_enter)
        self.assertGreater(t_enter, 0.0)
        self.assertGreater(t_exit, t_enter)

    def test_receding_trajectory_rejected(self):
        """
        CRITICAL TEST: Verifies that debris moving AWAY from the corridor on the same line
        does NOT trigger an intersection (solves teammate's infinite-line false alarm).
        """
        # Same line as above, but velocity is inverted [-28, -23] (receding into distance)
        hit, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=20.0, v0=20.0, vx=-28.0, vy=-23.0, bounds=self.bounds
        )
        self.assertFalse(hit)
        self.assertIsNone(t_enter)

    def test_parallel_miss(self):
        # Origin at (20, 20), moving horizontally [30, 0] across top. Never enters v in [100, 400]
        hit, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=20.0, v0=20.0, vx=30.0, vy=0.0, bounds=self.bounds
        )
        self.assertFalse(hit)
        self.assertIsNone(t_enter)

    def test_steep_vertical_penetration(self):
        # Origin at (300, 10), moving vertically downwards [0, 40] directly through box
        hit, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=300.0, v0=10.0, vx=0.0, vy=40.0, bounds=self.bounds
        )
        self.assertTrue(hit)
        # Enters at v = 100 -> t = (100 - 10) / 40 = 2.25s
        self.assertAlmostEqual(t_enter, 2.25, places=2)
        # Exits at v = 400 -> t = (400 - 10) / 40 = 9.75s
        self.assertAlmostEqual(t_exit, 9.75, places=2)

    def test_origin_already_inside_box(self):
        # Origin at center (300, 250) moving at [10, 10]
        hit, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=300.0, v0=250.0, vx=10.0, vy=10.0, bounds=self.bounds
        )
        self.assertTrue(hit)
        self.assertEqual(t_enter, 0.0)
        self.assertGreater(t_exit, 0.0)


class TestPinholeCorridorEvaluator(unittest.IsolatedAsyncioTestCase):
    """Tests PinholeCorridorEvaluator integration with Phase 2 DebrisDetections."""

    async def test_hit_detections_confirmed(self):
        evaluator = PinholeCorridorEvaluator()
        bounds = evaluator.projector.get_corridor_2d_bounds()
        center_u = (bounds[0] + bounds[2]) / 2.0
        center_v = (bounds[1] + bounds[3]) / 2.0

        # Create 5 detections tracking from top-left towards center
        dets = []
        x, y = 50.0, 50.0
        dx = (center_u - 50.0) / 10.0
        dy = (center_v - 50.0) / 10.0
        for i in range(5):
            dets.append(
                DebrisDetection(
                    detected=True,
                    centroid_x=x,
                    centroid_y=y,
                    velocity_vx=dx * 10.0,
                    velocity_vy=dy * 10.0,
                    frame_index=i,
                )
            )
            x += dx
            y += dy

        assessment = await evaluator.evaluate_corridor("Threat_Alpha", dets)
        self.assertTrue(assessment.corridor_intersected)
        self.assertEqual(assessment.target_asset, "Threat_Alpha")
        self.assertGreater(assessment.confidence_score, 0.8)

    async def test_miss_detections_cleared(self):
        evaluator = PinholeCorridorEvaluator()
        # Detections skirting horizontally along y = 10 px (far above corridor bounds)
        dets = []
        x, y = 50.0, 10.0
        for i in range(5):
            dets.append(
                DebrisDetection(
                    detected=True,
                    centroid_x=x,
                    centroid_y=y,
                    velocity_vx=15.0,
                    velocity_vy=0.0,
                    frame_index=i,
                )
            )
            x += 15.0

        assessment = await evaluator.evaluate_corridor("Debris_Clear", dets)
        self.assertFalse(assessment.corridor_intersected)


class TestTeammateScriptUpgrade(unittest.TestCase):
    """Verifies that Autonomous-Collision-Avoidance-main/edge_node.py runs cleanly."""

    def test_teammate_check_intersection_hit_and_miss(self):
        if teammate_check_intersection is None:
            self.skipTest("teammate edge_node.py not found")

        corridor = (
            teammate_project_corridor()
            if teammate_project_corridor
            else (198.47, 118.47, 441.53, 361.53)
        )

        # Simulation 1 (Miss streak): Moving horizontally at top [50, 50], [65, 52], [80, 54]...
        miss_centroids = [(50 + 15 * i, 50 + 2 * i) for i in range(6)]
        self.assertFalse(teammate_check_intersection(miss_centroids, corridor))

        # Simulation 2 (Hit streak): Moving into center [50, 50], [68, 62], [86, 74]...
        hit_centroids = [(50 + 18 * i, 50 + 12 * i) for i in range(6)]
        self.assertTrue(teammate_check_intersection(hit_centroids, corridor))

        # Receding trajectory (Moving backwards away from corridor)
        receding_centroids = [(50 - 18 * i, 50 - 12 * i) for i in range(6)]
        self.assertFalse(teammate_check_intersection(receding_centroids, corridor))


if __name__ == "__main__":
    unittest.main()
