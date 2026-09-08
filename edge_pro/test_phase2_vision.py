"""
Project Kessler - Edge Processing Payload
Phase 2 Unit & Integration Test Suite (Frame Differencing & Streak Extraction)
-------------------------------------------------------------------------------
Validates:
1. Static background starfield elimination via OpenCV frame differencing.
2. High-velocity transient streak extraction and subpixel centroid calculation.
3. Morphological noise rejection for isolated 1-pixel thermal dark noise.
4. 2D Trajectory and velocity vector estimation [Vx, Vy] for Phase 3.
5. Integration with PayloadManager and state machine lifecycle.
"""

import math
import time
import unittest
import numpy as np
import cv2

from edge_pro.opencv_vision import OpenCVVisionPipeline
from edge_pro.vision_interface import DebrisDetection
from edge_pro.payload_manager import PayloadManager
from edge_pro.config import PayloadConfig
from edge_pro.state_machine import PayloadState


class TestPhase2VisionPipeline(unittest.TestCase):
    """Tests the computer vision algorithms of the Phase 2 OpenCV pipeline."""

    def setUp(self):
        self.width = 640
        self.height = 480
        self.pipeline = OpenCVVisionPipeline(
            differencing_mode="consecutive",
            threshold_val=25,
            min_streak_area=3.0,
            target_asset="Debris_Obj_8492",
        )
        self.pipeline.attitude_locked = True

    def _generate_starfield(self, num_stars: int = 50, seed: int = 42) -> np.ndarray:
        """Helper to render a synthetic deep space starfield."""
        np.random.seed(seed)
        img = np.zeros((self.height, self.width), dtype=np.uint8)
        for _ in range(num_stars):
            x = np.random.randint(10, self.width - 10)
            y = np.random.randint(10, self.height - 10)
            cv2.circle(img, (x, y), 1, int(np.random.randint(90, 240)), -1)
        return img

    def test_static_stars_eliminated(self):
        """Verifies that frame differencing eliminates 100% of static background stars."""
        starfield_1 = self._generate_starfield(num_stars=50)
        starfield_2 = starfield_1.copy()  # Static stars, no motion

        # First frame initializes reference
        det_1 = self.pipeline.process_frame(starfield_1)
        self.assertIsNone(det_1)

        # Second identical frame must yield NO detections (all static stars subtracted to 0)
        det_2 = self.pipeline.process_frame(starfield_2)
        self.assertIsNone(det_2)

    def test_moving_streak_isolated(self):
        """Verifies that an injected transient debris streak is isolated and its centroid extracted."""
        starfield = self._generate_starfield(num_stars=50)

        # Frame 1: Baseline starfield
        self.pipeline.process_frame(starfield)

        # Frame 2: Starfield + high-velocity streak from (200, 150) to (230, 170)
        streak_frame = starfield.copy()
        cv2.line(streak_frame, (200, 150), (230, 170), 255, 2)
        cv2.circle(streak_frame, (230, 170), 2, 255, -1)

        det = self.pipeline.process_frame(streak_frame)
        self.assertIsNotNone(det)
        self.assertTrue(det.detected)

        # Expected centroid is around (215, 160)
        self.assertAlmostEqual(det.centroid_x, 215.0, delta=5.0)
        self.assertAlmostEqual(det.centroid_y, 160.0, delta=5.0)
        self.assertGreater(det.streak_length_px, 20.0)

    def test_noise_rejection_isolated_pixels(self):
        """Verifies that 1-pixel thermal noise / cosmic rays are rejected by contour filtering."""
        starfield = self._generate_starfield(num_stars=30)
        self.pipeline.process_frame(starfield)

        # Inject 1-pixel thermal noise blips
        noise_frame = starfield.copy()
        noise_frame[50, 50] = 255
        noise_frame[120, 300] = 255
        noise_frame[400, 200] = 255

        det = self.pipeline.process_frame(noise_frame)
        # Should be rejected because area < min_streak_area
        self.assertIsNone(det)

    def test_velocity_vector_estimation(self):
        """Verifies that 2D velocity vector [Vx, Vy] and heading angle are computed accurately."""
        starfield = self._generate_starfield(num_stars=40)
        self.pipeline.process_frame(starfield, current_time=0.0)

        # Frame 1 with streak centered around (100, 100) at t = 0.1s
        frame_1 = starfield.copy()
        cv2.line(frame_1, (90, 90), (110, 110), 255, 2)
        det_1 = self.pipeline.process_frame(frame_1, current_time=0.1)
        self.assertIsNotNone(det_1)

        # Frame 2 with streak shifted by (+20, +10) at t = 0.2s (dt = 0.1s)
        # Expected velocity: Vx ≈ 200 px/s, Vy ≈ 100 px/s, Heading ≈ 26.56°
        frame_2 = starfield.copy()
        cv2.line(frame_2, (110, 100), (130, 120), 255, 2)
        det_2 = self.pipeline.process_frame(frame_2, current_time=0.2)
        self.assertIsNotNone(det_2)

        self.assertIsNotNone(det_2.velocity_vx)
        self.assertIsNotNone(det_2.velocity_vy)
        self.assertAlmostEqual(det_2.velocity_vx, 200.0, delta=30.0)
        self.assertAlmostEqual(det_2.velocity_vy, 100.0, delta=30.0)
        self.assertAlmostEqual(det_2.heading_angle_deg, 26.6, delta=5.0)

    def test_pipeline_integration_with_payload_manager(self):
        """Verifies OpenCVVisionPipeline plugs seamlessly into PayloadManager."""
        custom_pipeline = OpenCVVisionPipeline(
            differencing_mode="hybrid",
            threshold_val=25,
            target_asset="Debris_Obj_8492"
        )
        config = PayloadConfig(
            observation_lead_time_seconds=1800.0,
            immediate_dispatch=True,
            log_level="WARNING",
        )
        manager = PayloadManager(config=config, vision_pipeline=custom_pipeline)
        self.assertEqual(manager.vision, custom_pipeline)
        self.assertFalse(custom_pipeline.is_active)


if __name__ == "__main__":
    unittest.main()
