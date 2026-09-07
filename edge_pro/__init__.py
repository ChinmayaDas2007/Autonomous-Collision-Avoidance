"""
Project Kessler - Edge Processing Payload (edge_pro)
Phase 1: Mission Management & Slew Targeting
Phase 2: Frame Differencing & Streak Extraction
-------------------------------------------------------------------------------
Autonomous orbital collision avoidance edge compute software package.
"""

from edge_pro.config import PayloadConfig
from edge_pro.models import (
    RefinedCDM,
    CDMHeader,
    ConjunctionData,
    AIDragPrediction,
    SlewCommand,
    PacketValidationError,
)
from edge_pro.scheduler import (
    ObservationScheduler,
    ObservationSchedule,
    parse_iso8601_utc,
    format_iso8601_utc,
)
from edge_pro.state_machine import (
    PayloadStateMachine,
    PayloadState,
    StateTransitionError,
)
from edge_pro.tcp_server import TCPIngestServer
from edge_pro.tcp_client import TCPSlewPublisher
from edge_pro.vision_interface import (
    VisionPipelineInterface,
    MockVisionPipeline,
    DebrisDetection,
)
from edge_pro.payload_manager import PayloadManager

try:
    from edge_pro.opencv_vision import OpenCVVisionPipeline
except ImportError:
    OpenCVVisionPipeline = None

__version__ = "2.0.0"
__all__ = [
    "PayloadManager",
    "PayloadConfig",
    "RefinedCDM",
    "CDMHeader",
    "ConjunctionData",
    "AIDragPrediction",
    "SlewCommand",
    "PacketValidationError",
    "PayloadStateMachine",
    "PayloadState",
    "StateTransitionError",
    "ObservationScheduler",
    "ObservationSchedule",
    "parse_iso8601_utc",
    "format_iso8601_utc",
    "TCPIngestServer",
    "TCPSlewPublisher",
    "VisionPipelineInterface",
    "MockVisionPipeline",
    "DebrisDetection",
    "OpenCVVisionPipeline",
]
