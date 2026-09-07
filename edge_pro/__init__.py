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
    SpacecraftCapability,
    CorridorAssessment,
    ManeuverDecisionPacket,
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
from edge_pro.corridor_interface import (
    DangerCorridorEvaluator,
    MockCorridorEvaluator,
)
from edge_pro.danger_corridor import (
    PinholeCorridorProjector,
    PinholeCorridorEvaluator,
    liang_barsky_ray_aabb_intersect,
)
from edge_pro.decision_engine import (
    ActiveActiveDecisionEngine,
    BASELINE_DELTA_V_MPS,
    BASELINE_LIFETIME_YEARS,
    BASELINE_DOWNTIME_SEC,
    PRIORITY_COST_MAP,
)
from edge_pro.decision_publisher import (
    TCPDecisionPublisher,
    TCPDecisionBroadcastServer,
)
from edge_pro.payload_manager import PayloadManager

try:
    from edge_pro.opencv_vision import OpenCVVisionPipeline
except ImportError:
    OpenCVVisionPipeline = None

__version__ = "4.0.0"
__all__ = [
    "PayloadManager",
    "PayloadConfig",
    "RefinedCDM",
    "CDMHeader",
    "ConjunctionData",
    "AIDragPrediction",
    "SlewCommand",
    "PacketValidationError",
    "SpacecraftCapability",
    "CorridorAssessment",
    "ManeuverDecisionPacket",
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
    "DangerCorridorEvaluator",
    "MockCorridorEvaluator",
    "PinholeCorridorProjector",
    "PinholeCorridorEvaluator",
    "liang_barsky_ray_aabb_intersect",
    "ActiveActiveDecisionEngine",
    "TCPDecisionPublisher",
    "TCPDecisionBroadcastServer",
]
