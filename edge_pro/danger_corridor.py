"""
Project Kessler - Edge Processing Payload
Phase 3: Production Danger Corridor Projection & Directed Ray Intersection
-------------------------------------------------------------------------------
Provides deterministic 3D-to-2D danger corridor projection and robust directed
ray-to-box intersection:
1. Pinhole camera matrix projection based on 15° Field of View and sensor resolution.
2. 3D spacecraft safety volume (1 km x 1 km x 0.5 km) projected to 2D pixel space (AABB).
3. Liang-Barsky directed ray clipping against 2D AABB for forward time t >= 0.
   - Eliminates false positive collision warnings for receding/diverging debris.
   - Deterministic handling of steep vertical and horizontal trajectories (no slope blow-up).
   - Zero random jitter needed.
4. Production implementation of `DangerCorridorEvaluator` interfacing Phase 2 to Phase 4.
"""

from datetime import datetime, timezone
import logging
import math
from typing import List, Optional, Tuple

from edge_pro.corridor_interface import DangerCorridorEvaluator
from edge_pro.models import CorridorAssessment
from edge_pro.vision_interface import DebrisDetection

logger = logging.getLogger("edge_pro.corridor.pinhole")


class PinholeCorridorProjector:
    """
    Computes camera intrinsics and projects 3D spacecraft safety volumes
    into 2D pixel bounding boxes (Axis-Aligned Bounding Boxes - AABB).
    """

    def __init__(
        self,
        image_width: int = 640,
        image_height: int = 480,
        fov_degrees: float = 15.0,
        safety_box_half_w_m: float = 500.0,   # 1 km full width
        safety_box_half_h_m: float = 500.0,   # 1 km full height
        nominal_z_depth_m: float = 10000.0,   # Stand-off observation distance (10 km)
    ):
        self.img_w = image_width
        self.img_h = image_height
        self.fov_deg = fov_degrees
        self.safety_half_w = safety_box_half_w_m
        self.safety_half_h = safety_box_half_h_m
        self.z_depth = nominal_z_depth_m

        # Pinhole focal length f = (W / 2) / tan(FOV / 2)
        fov_rad = math.radians(fov_degrees)
        self.focal_length = (image_width / 2.0) / math.tan(fov_rad / 2.0)
        self.cx = image_width / 2.0
        self.cy = image_height / 2.0

    def project_3d_to_pixel(self, X: float, Y: float, Z: float) -> Tuple[float, float]:
        """Projects a 3D point (X, Y, Z) in the camera frame to 2D pixel (u, v)."""
        if Z <= 0.0:
            raise ValueError(f"Z depth must be positive for camera projection, got {Z}")
        u = self.focal_length * (X / Z) + self.cx
        v = self.focal_length * (Y / Z) + self.cy
        return u, v

    def pixel_to_meters_at_depth(self, pixel_dist: float, Z: Optional[float] = None) -> float:
        """Converts 2D pixel distance back to physical meters at a given depth Z."""
        depth = Z or self.z_depth
        return (pixel_dist / self.focal_length) * depth

    def get_corridor_2d_bounds(self, z_depth: Optional[float] = None) -> Tuple[float, float, float, float]:
        """
        Projects the 4 boundary corners of the 3D safety box into 2D pixel space.
        Returns (min_u, min_v, max_u, max_v).
        """
        Z = z_depth or self.z_depth
        pts_3d = [
            (-self.safety_half_w, -self.safety_half_h, Z),
            ( self.safety_half_w, -self.safety_half_h, Z),
            (-self.safety_half_w,  self.safety_half_h, Z),
            ( self.safety_half_w,  self.safety_half_h, Z),
        ]
        us = []
        vs = []
        for X, Y, z in pts_3d:
            u, v = self.project_3d_to_pixel(X, Y, z)
            us.append(u)
            vs.append(v)

        return min(us), min(vs), max(us), max(vs)


def liang_barsky_ray_aabb_intersect(
    u0: float,
    v0: float,
    vx: float,
    vy: float,
    bounds: Tuple[float, float, float, float],
    max_time_sec: float = 600.0,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Evaluates directed ray intersection against an Axis-Aligned Bounding Box (AABB)
    using the Liang-Barsky parametric clipping algorithm for forward time t >= 0.

    Ray equation:
      u(t) = u0 + vx * t
      v(t) = v0 + vy * t    for 0 <= t <= max_time_sec

    Args:
        u0, v0: Ray origin (streak centroid in pixels)
        vx, vy: 2D velocity vector in px/s
        bounds: (min_u, min_v, max_u, max_v)
        max_time_sec: Maximum forward time horizon to check

    Returns:
        (intersects, t_enter, t_exit)
        - intersects: True if forward ray enters/traverses box, False otherwise.
        - t_enter: Time in seconds when ray enters the box (or 0.0 if starting inside).
        - t_exit: Time in seconds when ray exits the box.
    """
    min_u, min_v, max_u, max_v = bounds

    # If speed is virtually zero, check if point is currently inside box
    speed_sq = vx * vx + vy * vy
    if speed_sq < 1e-6:
        inside = (min_u <= u0 <= max_u) and (min_v <= v0 <= max_v)
        return inside, (0.0 if inside else None), (0.0 if inside else None)

    # 4 boundary clipping planes
    p = [-vx, vx, -vy, vy]
    q = [u0 - min_u, max_u - u0, v0 - min_v, max_v - v0]

    t0 = 0.0
    t1 = max_time_sec

    for i in range(4):
        pi = p[i]
        qi = q[i]

        if abs(pi) < 1e-9:
            # Ray is parallel to the boundary plane
            if qi < 0:
                # Origin is outside the boundary plane -> completely misses
                return False, None, None
        else:
            t = qi / pi
            if pi < 0:
                # Entering boundary
                if t > t0:
                    t0 = t
            else:
                # Exiting boundary
                if t < t1:
                    t1 = t

        if t0 > t1:
            # Ray misses the box
            return False, None, None

    # Intersection confirmed for t in [t0, t1] with t1 >= 0
    return True, max(0.0, t0), t1


class PinholeCorridorEvaluator(DangerCorridorEvaluator):
    """
    Production-grade Phase 3 Danger Corridor Evaluator for Project Kessler.
    Integrates optical debris centroid extractions from Phase 2 and tests
    directed trajectory intersection against the 3D safety corridor.
    """

    def __init__(
        self,
        projector: Optional[PinholeCorridorProjector] = None,
        min_detections_required: int = 2,
    ):
        self.projector = projector or PinholeCorridorProjector()
        self.min_detections_required = min_detections_required

    async def evaluate_corridor(
        self,
        target_asset: str,
        detections: List[DebrisDetection],
        time_to_closest_approach_sec: float = 5520.0,
        nominal_miss_distance_m: float = 450.0,
    ) -> CorridorAssessment:
        """
        Evaluates whether detected debris streaks intersect the danger corridor.
        Uses Liang-Barsky directed ray tracing to eliminate false alarms.
        """
        bounds = self.projector.get_corridor_2d_bounds()
        min_u, min_v, max_u, max_v = bounds
        center_u = (min_u + max_u) / 2.0
        center_v = (min_v + max_v) / 2.0

        # Filter valid detections with coordinates
        valid_dets = [
            d for d in detections
            if d.centroid_x is not None and d.centroid_y is not None
        ]

        if len(valid_dets) < self.min_detections_required:
            logger.warning(
                f"[Corridor] Insufficient detections ({len(valid_dets)}/{self.min_detections_required}) "
                f"for robust ray fit on '{target_asset}'. Falling back to nominal range."
            )
            intersected = nominal_miss_distance_m < self.projector.safety_half_w
            return CorridorAssessment(
                target_asset=target_asset,
                corridor_intersected=intersected,
                time_to_closest_approach_sec=time_to_closest_approach_sec,
                miss_distance_m=nominal_miss_distance_m,
                threat_vector_normalized=[-0.7071, 0.0, 0.7071],
                confidence_score=0.60,
            )

        # Extract latest state
        latest_det = valid_dets[-1]
        u0 = float(latest_det.centroid_x)
        v0 = float(latest_det.centroid_y)

        # Determine velocity vector [Vx, Vy]
        vx = latest_det.velocity_vx
        vy = latest_det.velocity_vy

        # If velocity was not directly supplied by tracker, compute difference from history
        if vx is None or vy is None or (abs(vx) < 1e-4 and abs(vy) < 1e-4):
            first_det = valid_dets[0]
            dx = u0 - float(first_det.centroid_x)
            dy = v0 - float(first_det.centroid_y)
            # Default to 10 Hz frame rate assumption if delta t not timestamped
            dt = max(0.1, len(valid_dets) * 0.1)
            vx = dx / dt
            vy = dy / dt

        # Evaluate directed ray intersection with 2D corridor AABB
        intersected, t_enter, t_exit = liang_barsky_ray_aabb_intersect(
            u0=u0,
            v0=v0,
            vx=vx,
            vy=vy,
            bounds=bounds,
            max_time_sec=time_to_closest_approach_sec,
        )

        # Calculate minimum distance from forward ray to center of safety box
        speed = math.hypot(vx, vy)
        if speed > 1e-6:
            uvx = vx / speed
            uvy = vy / speed
            # Vector from ray origin to box center
            to_center_x = center_u - u0
            to_center_y = center_v - v0
            # Project onto forward ray (t >= 0)
            proj = max(0.0, to_center_x * uvx + to_center_y * uvy)
            closest_u = u0 + uvx * proj
            closest_v = v0 + uvy * proj
            pixel_miss = math.hypot(center_u - closest_u, center_v - closest_v)
        else:
            pixel_miss = math.hypot(center_u - u0, center_v - v0)
            uvx, uvy = 0.0, 1.0

        # Physical distance in meters
        miss_m = self.projector.pixel_to_meters_at_depth(pixel_miss)

        threat_vec = [float(uvx), float(uvy), 0.0]
        confidence = min(1.0, 0.70 + 0.05 * len(valid_dets))

        logger.info(
            f"[Phase 3 Corridor] Target: '{target_asset}' | Detections: {len(valid_dets)} | "
            f"Ray Origin: ({u0:.1f}, {v0:.1f}) px | V: [{vx:.1f}, {vy:.1f}] px/s | "
            f"Breach Confirmed: {intersected} (Miss dist: {miss_m:.1f} m, t_enter: {t_enter})"
        )

        return CorridorAssessment(
            target_asset=target_asset,
            corridor_intersected=intersected,
            time_to_closest_approach_sec=t_enter if (intersected and t_enter is not None) else time_to_closest_approach_sec,
            miss_distance_m=miss_m,
            threat_vector_normalized=threat_vec,
            confidence_score=confidence,
        )
