"""
Project Kessler - Edge Processing Payload
Configuration Management Module (Phase 1)
-------------------------------------------------------------------------------
Provides centralized, strictly typed configuration settings for TCP sockets,
observation scheduling parameters, mission simulation modes, and telemetry logging.
"""

from dataclasses import dataclass, field
import os
from typing import Optional


@dataclass
class PayloadConfig:
    """Configuration parameters for the Edge Processing Payload."""

    # TCP Ingestion Listener (Ground AI Interface)
    ingest_host: str = field(
        default_factory=lambda: os.getenv("EDGE_INGEST_HOST", "127.0.0.1")
    )
    ingest_port: int = field(
        default_factory=lambda: int(os.getenv("EDGE_INGEST_PORT", "5555"))
    )

    # TCP Publisher Target (core_phy / Basilisk FSW Interface)
    core_phy_host: str = field(
        default_factory=lambda: os.getenv("CORE_PHY_HOST", "127.0.0.1")
    )
    core_phy_port: int = field(
        default_factory=lambda: int(os.getenv("CORE_PHY_PORT", "5556"))
    )

    # Observation Scheduling Parameters
    # Default lead time is 30 minutes before TCA (T_0 = TCA - 30 min)
    observation_lead_time_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("EDGE_OBSERVATION_LEAD_TIME_SEC", "1800.0")
        )
    )

    # Duration the optical tracking sensor remains active during the observation window
    tracking_duration_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("EDGE_TRACKING_DURATION_SEC", "300.0")
        )
    )

    # Hackathon & Hardware-In-The-Loop Simulation Modes
    # If True: Dispatches slew command immediately upon CDM ingestion (bypasses wall-clock sleep)
    immediate_dispatch: bool = field(
        default_factory=lambda: os.getenv("EDGE_IMMEDIATE_DISPATCH", "false").lower()
        in ("1", "true", "yes")
    )

    # Multiplier for accelerated mission clocks in SIL simulations (e.g. 10.0 = 10x speed)
    time_scale_factor: float = field(
        default_factory=lambda: float(os.getenv("EDGE_TIME_SCALE_FACTOR", "1.0"))
    )

    # Networking & Stream Framing Constraints
    max_packet_bytes: int = 65536
    reconnect_interval_seconds: float = 2.0
    max_reconnect_attempts: Optional[int] = None  # None = infinite retries

    # Telemetry and Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("EDGE_LOG_LEVEL", "INFO").upper()
    )
    log_format: str = (
        "[%(asctime)s.%(msecs)03d UTC] [%(levelname)s] [%(name)s] [%(filename)s:%(lineno)d]: %(message)s"
    )
    date_format: str = "%Y-%m-%dT%H:%M:%S"
