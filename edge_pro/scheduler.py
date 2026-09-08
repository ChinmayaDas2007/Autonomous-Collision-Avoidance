"""
Project Kessler - Edge Processing Payload
Observation Scheduling & UTC ISO 8601 Parsing (Phase 1)
-------------------------------------------------------------------------------
The Edge Payload is a pure execution terminal: observation scheduling
and line-of-sight / eclipse calculations are performed upstream by Ground AI.
This module parses ISO 8601 timestamps, validates UTC formatting, extracts
the Ground-AI-provided observation_window_start_utc, and schedules mission timers.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional

from edge_pro.models import RefinedCDM, SlewCommand, PacketValidationError

logger = logging.getLogger("edge_pro.scheduler")


def parse_iso8601_utc(ts_str: str) -> datetime:
    """
    Parses an ISO 8601 timestamp string and normalizes it to a timezone-aware UTC datetime.
    Accepts formats such as:
      - "2026-09-08T14:32:15Z"
      - "2026-09-08T14:32:15.123Z"
      - "2026-09-08T14:32:15+00:00"
      - "2026-09-08T14:32:15-05:00"
    """
    if not ts_str or not isinstance(ts_str, str):
        raise PacketValidationError(f"Invalid timestamp: {ts_str}")

    cleaned = ts_str.strip()
    if cleaned.endswith("Z") or cleaned.endswith("z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as err:
        raise PacketValidationError(f"Cannot parse ISO 8601 timestamp '{ts_str}': {err}") from err

    if dt.tzinfo is None:
        logger.warning(f"Timestamp '{ts_str}' is naive; assuming UTC.")
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt


def format_iso8601_utc(dt: datetime) -> str:
    """
    Formats a datetime object into a strict UTC ISO 8601 string ending with 'Z'.
    Example: 2026-09-08T14:02:15Z
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    if dt.microsecond > 0:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ObservationSchedule:
    """Observation window and command payload for a conjunction event."""
    cdm: RefinedCDM
    tca_utc: datetime
    execute_at_utc: datetime
    lead_time_seconds: float
    slew_command: SlewCommand

    @property
    def target_asset(self) -> str:
        return self.cdm.conjunction_data.secondary_asset

    @property
    def primary_asset(self) -> str:
        return self.cdm.conjunction_data.primary_asset

    def seconds_until_execution(self, current_time: Optional[datetime] = None) -> float:
        """Returns signed seconds until observation window opens (negative if passed)."""
        now = current_time or datetime.now(timezone.utc)
        return (self.execute_at_utc - now).total_seconds()


class ObservationScheduler:
    """
    Manages mission observation scheduling and timer execution for the Edge Payload.
    The Edge Payload does not calculate observation windows or lead times;
    it ingests the exact observation_window_start_utc computed upstream by Ground AI.
    """

    def __init__(
        self,
        immediate_dispatch: bool = False,
        time_scale_factor: float = 1.0,
    ):
        self.immediate_dispatch = immediate_dispatch
        self.time_scale_factor = max(0.001, time_scale_factor)
        self.active_schedules: list[ObservationSchedule] = []

    def plan_observation(self, cdm: RefinedCDM) -> ObservationSchedule:
        """
        Extracts Ground-AI-calculated observation_window_start_utc from incoming CDM
        and prepares the SlewCommand for the FSM.
        """
        raw_tca = cdm.conjunction_data.time_of_closest_approach
        tca_dt = parse_iso8601_utc(raw_tca)

        # Ground AI owns orbital mechanics: parse Ground-AI computed window directly
        raw_obs_start = cdm.conjunction_data.observation_window_start_utc
        execute_at_dt = parse_iso8601_utc(raw_obs_start)
        execute_at_str = format_iso8601_utc(execute_at_dt)

        lead_time_sec = (tca_dt - execute_at_dt).total_seconds()

        # Create output slew command matching core_phy contract
        slew_cmd = SlewCommand(
            command="SLEW_TO_TARGET",
            target_asset=cdm.conjunction_data.secondary_asset,
            execute_at_utc=execute_at_str,
            sensor_mode="OPTICAL_TRACKING",
        )
        slew_cmd.validate()

        schedule = ObservationSchedule(
            cdm=cdm,
            tca_utc=tca_dt,
            execute_at_utc=execute_at_dt,
            lead_time_seconds=lead_time_sec,
            slew_command=slew_cmd,
        )

        self.active_schedules.append(schedule)

        logger.info(
            f"Ground AI Observation Schedule Ingested:\n"
            f"  Primary Asset:        {schedule.primary_asset}\n"
            f"  Target Threat:        {schedule.target_asset}\n"
            f"  TCA (UTC):            {format_iso8601_utc(tca_dt)}\n"
            f"  Observation T_0 (UTC):{execute_at_str} (Lead Time: {lead_time_sec / 60.0:.1f}m ahead of TCA)"
        )
        return schedule
