"""
Project Kessler - Edge Processing Payload
Vision Pipeline Interface (Phase 1 / Phase 2 Bridge)
-------------------------------------------------------------------------------
Defines the modular abstraction layer connecting Mission Management & Slew
Targeting (Phase 1) to real-time Star Tracker frame differencing and streak
extraction (Phase 2).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Optional, Tuple

logger = logging.getLogger("edge_pro.vision")


@dataclass
class DebrisDetection:
    """
    Telemetry packet output when a transient streak is isolated in the Star Tracker FOV.
    Carries 2D trajectory vectors and bounding box required for Phase 3 Danger Corridor Projection.
    """
    detected: bool
    centroid_x: Optional[float] = None
    centroid_y: Optional[float] = None
    velocity_vx: Optional[float] = None       # Pixel velocity X (px/s)
    velocity_vy: Optional[float] = None       # Pixel velocity Y (px/s)
    velocity_speed: Optional[float] = None    # Speed norm (px/s)
    heading_angle_deg: Optional[float] = None # Direction of motion (degrees)
    bounding_box: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    streak_length_px: Optional[float] = None
    contour_area: Optional[float] = None
    intensity_snr: Optional[float] = None
    frame_index: Optional[int] = None
    timestamp_utc: Optional[str] = None
    target_asset: Optional[str] = None


class VisionPipelineInterface(ABC):
    """
    Abstract contract for the Edge Vision processing pipeline.
    Phase 2 will implement frame differencing, background star removal,
    and centroid extraction on top of this interface.
    """

    @abstractmethod
    async def arm_sensor(self, target_asset: str) -> None:
        """Configures optical sensor parameters in preparation for observation window."""
        pass

    @abstractmethod
    async def start_tracking(self) -> None:
        """Begins ingesting Star Tracker video frames and executing frame differencing."""
        pass

    @abstractmethod
    async def stop_tracking(self) -> None:
        """Halts frame processing and releases video buffer memory."""
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """Returns True if optical tracking is actively executing."""
        pass


class MockVisionPipeline(VisionPipelineInterface):
    """
    Production-ready stub implementation for Phase 1 testing.
    Provides non-blocking simulation of the vision pipeline before Phase 2
    OpenCV integration is appended.
    """

    def __init__(self):
        self._active: bool = False
        self._target_asset: Optional[str] = None

    async def arm_sensor(self, target_asset: str) -> None:
        self._target_asset = target_asset
        logger.info(f"[Vision Hook] Optical Star Tracker armed for asset: {target_asset}")

    async def start_tracking(self) -> None:
        self._active = True
        logger.info(
            f"[Vision Hook] Optical tracking ENGAGED on target '{self._target_asset}'. "
            f"Frame differencing pipeline active."
        )

    async def stop_tracking(self) -> None:
        self._active = False
        logger.info("[Vision Hook] Optical tracking DISENGAGED. Frame buffers flushed.")

    def is_active(self) -> bool:
        return self._active

    def simulate_detection(self, x: float = 256.0, y: float = 256.0) -> DebrisDetection:
        """Helper to inject a synthetic detection event for testing Phase 1 integration."""
        return DebrisDetection(
            detected=True,
            centroid_x=x,
            centroid_y=y,
            intensity_snr=18.4,
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            target_asset=self._target_asset,
        )
