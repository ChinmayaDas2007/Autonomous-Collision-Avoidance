"""
Project Kessler - Edge Processing Payload
Phase 3 Teammate Integration Interface: Danger Corridor Evaluator
-------------------------------------------------------------------------------
Defines the clean boundary interface between Phase 3 (Danger Corridor Projection)
and Phase 4 (Go/No-Go Decision Engine).

Your teammate implementing Phase 3 will inherit from `DangerCorridorEvaluator`
and implement `evaluate_corridor()`.
"""

from abc import ABC, abstractmethod
import logging
from typing import List, Optional

from edge_pro.models import CorridorAssessment
from edge_pro.vision_interface import DebrisDetection

logger = logging.getLogger("edge_pro.corridor")


class DangerCorridorEvaluator(ABC):
    """
    Abstract interface for Phase 3 Danger Corridor Projection.
    Takes debris streak detections and projects the 3D safety box onto the 2D plane
    to verify if the threat trajectory intersects the spacecraft safety corridor.
    """

    @abstractmethod
    async def evaluate_corridor(
        self,
        target_asset: str,
        detections: List[DebrisDetection],
        time_to_closest_approach_sec: float = 5520.0,
        nominal_miss_distance_m: float = 450.0,
    ) -> CorridorAssessment:
        """
        Evaluates whether detected debris streaks intersect the danger corridor.

        Args:
            target_asset: Identifier of the threat asset (e.g. 'Debris_Obj_8492').
            detections: List of DebrisDetection frames extracted in Phase 2.
            time_to_closest_approach_sec: Seconds remaining until TCA.
            nominal_miss_distance_m: Estimated miss distance in meters.

        Returns:
            CorridorAssessment containing boolean intersection and threat vector.
        """
        pass


class MockCorridorEvaluator(DangerCorridorEvaluator):
    """
    Mock implementation of Phase 3 for standalone testing, SIL simulation,
    and unblocking Phase 4 development prior to teammate merge.
    """

    def __init__(
        self,
        force_intersection: bool = True,
        simulated_miss_distance_m: float = 350.0,
        simulated_threat_vector: Optional[List[float]] = None,
    ):
        self.force_intersection = force_intersection
        self.simulated_miss_distance_m = simulated_miss_distance_m
        self.simulated_threat_vector = simulated_threat_vector or [-0.7071, 0.0, 0.7071]

    def set_intersection(self, intersected: bool, miss_distance_m: float = 350.0) -> None:
        """Dynamically toggles whether the mock evaluates to an intersection or miss."""
        self.force_intersection = intersected
        self.simulated_miss_distance_m = miss_distance_m

    async def evaluate_corridor(
        self,
        target_asset: str,
        detections: List[DebrisDetection],
        time_to_closest_approach_sec: float = 5520.0,
        nominal_miss_distance_m: float = 450.0,
    ) -> CorridorAssessment:
        """Produces a deterministic CorridorAssessment."""
        miss_dist = (
            self.simulated_miss_distance_m
            if self.force_intersection
            else max(1500.0, nominal_miss_distance_m)
        )

        logger.info(
            f"[Phase 3 Corridor Evaluation] Target: {target_asset} | "
            f"Detections evaluated: {len(detections)} | "
            f"Corridor Breached: {self.force_intersection} | "
            f"Estimated Miss Distance: {miss_dist:.1f} m"
        )

        return CorridorAssessment(
            target_asset=target_asset,
            corridor_intersected=self.force_intersection,
            time_to_closest_approach_sec=time_to_closest_approach_sec,
            miss_distance_m=miss_dist,
            threat_vector_normalized=self.simulated_threat_vector,
            confidence_score=0.98 if detections else 0.85,
        )
