"""
Project Kessler - Edge Processing Payload
Data Contracts & Strict Packet Validation (Phase 1)
-------------------------------------------------------------------------------
Defines strictly typed data models and validation logic for:
1. Ingested Refined Conjunction Data Messages (CDMs) from Ground AI.
2. Slew Targeting Commands dispatched to core_phy (Basilisk FSW).
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional


class PacketValidationError(ValueError):
    """Raised when a JSON packet fails schema validation or typing constraints."""
    pass


@dataclass
class CDMHeader:
    """Header metadata for an ingested Ground AI message."""
    type: str
    timestamp_utc: str

    def validate(self) -> None:
        if not self.type:
            raise PacketValidationError("CDMHeader missing required 'type' field.")
        if self.type != "REFINED_CDM":
            raise PacketValidationError(
                f"Invalid message type '{self.type}'. Expected 'REFINED_CDM'."
            )
        if not self.timestamp_utc:
            raise PacketValidationError("CDMHeader missing required 'timestamp_utc' field.")


@dataclass
class ConjunctionData:
    """Conjunction geometry and critical approach timing from Ground AI."""
    primary_asset: str
    secondary_asset: str
    time_of_closest_approach: str
    observation_window_start_utc: str
    miss_distance_km: Optional[float] = None

    def validate(self) -> None:
        if not self.primary_asset or not isinstance(self.primary_asset, str):
            raise PacketValidationError("conjunction_data requires valid 'primary_asset' string.")
        if not self.secondary_asset or not isinstance(self.secondary_asset, str):
            raise PacketValidationError("conjunction_data requires valid 'secondary_asset' string.")
        if not self.time_of_closest_approach or not isinstance(self.time_of_closest_approach, str):
            raise PacketValidationError(
                "conjunction_data requires valid ISO 8601 'time_of_closest_approach' string."
            )
        try:
            tca_str = self.time_of_closest_approach.strip()
            if tca_str.endswith("Z") or tca_str.endswith("z"):
                tca_str = tca_str[:-1] + "+00:00"
            datetime.fromisoformat(tca_str)
        except ValueError as err:
            raise PacketValidationError(
                f"conjunction_data 'time_of_closest_approach' is not valid ISO 8601: {err}"
            ) from err

        if not self.observation_window_start_utc or not isinstance(self.observation_window_start_utc, str):
            raise PacketValidationError(
                "conjunction_data requires valid ISO 8601 'observation_window_start_utc' string calculated by Ground AI."
            )
        try:
            obs_str = self.observation_window_start_utc.strip()
            if obs_str.endswith("Z") or obs_str.endswith("z"):
                obs_str = obs_str[:-1] + "+00:00"
            datetime.fromisoformat(obs_str)
        except ValueError as err:
            raise PacketValidationError(
                f"conjunction_data 'observation_window_start_utc' is not valid ISO 8601: {err}"
            ) from err

        if self.miss_distance_km is not None and not isinstance(self.miss_distance_km, (int, float)):
            raise PacketValidationError("conjunction_data 'miss_distance_km' must be numeric.")


@dataclass
class AIDragPrediction:
    """Optional atmospheric drag prediction telemetry from Ground AI XGBoost node."""
    f107_flux: Optional[float] = None
    kp_index: Optional[float] = None
    drag_multiplier: Optional[float] = None
    ellipsoid_covariance_matrix: Optional[List[float]] = None


@dataclass
class RefinedCDM:
    """Complete Refined Conjunction Data Message ingested from Ground AI."""
    header: CDMHeader
    conjunction_data: ConjunctionData
    ai_drag_prediction: Optional[AIDragPrediction] = None
    action: Optional[str] = None  # e.g. "RECOMMEND_OPTICAL_CONFIRMATION", "ABORT_VISION_USE_GROUND_RADAR"
    raw_payload: Optional[Dict[str, Any]] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefinedCDM":
        if not isinstance(data, dict):
            raise PacketValidationError(f"Expected JSON object dict, received {type(data).__name__}.")

        # Parse header
        raw_header = data.get("header")
        if not isinstance(raw_header, dict):
            raise PacketValidationError("Missing or invalid 'header' object in packet.")
        header = CDMHeader(
            type=str(raw_header.get("type", "")),
            timestamp_utc=str(raw_header.get("timestamp_utc", "")),
        )
        header.validate()

        # Parse conjunction_data
        raw_conj = data.get("conjunction_data")
        if not isinstance(raw_conj, dict):
            raise PacketValidationError("Missing or invalid 'conjunction_data' object in packet.")
        conjunction_data = ConjunctionData(
            primary_asset=str(raw_conj.get("primary_asset", "")),
            secondary_asset=str(raw_conj.get("secondary_asset", "")),
            time_of_closest_approach=str(raw_conj.get("time_of_closest_approach", "")),
            observation_window_start_utc=str(raw_conj.get("observation_window_start_utc", "")),
            miss_distance_km=float(raw_conj["miss_distance_km"]) if "miss_distance_km" in raw_conj else None,
        )
        conjunction_data.validate()

        # Parse optional drag prediction
        ai_drag: Optional[AIDragPrediction] = None
        if "ai_drag_prediction" in data and isinstance(data["ai_drag_prediction"], dict):
            ad = data["ai_drag_prediction"]
            ai_drag = AIDragPrediction(
                f107_flux=float(ad["f107_flux"]) if "f107_flux" in ad else None,
                kp_index=float(ad["kp_index"]) if "kp_index" in ad else None,
                drag_multiplier=float(ad["drag_multiplier"]) if "drag_multiplier" in ad else None,
                ellipsoid_covariance_matrix=list(ad["ellipsoid_covariance_matrix"])
                if "ellipsoid_covariance_matrix" in ad
                else None,
            )

        action = data.get("action")

        return cls(
            header=header,
            conjunction_data=conjunction_data,
            ai_drag_prediction=ai_drag,
            action=action,
            raw_payload=data,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "RefinedCDM":
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as err:
            raise PacketValidationError(f"Malformed JSON: {err}") from err
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "header": asdict(self.header),
            "conjunction_data": {
                k: v for k, v in asdict(self.conjunction_data).items() if v is not None
            },
        }
        if self.ai_drag_prediction:
            result["ai_drag_prediction"] = {
                k: v for k, v in asdict(self.ai_drag_prediction).items() if v is not None
            }
        if self.action:
            result["action"] = self.action
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


@dataclass
class SlewCommand:
    """
    Targeting packet dispatched from Edge Payload to core_phy (Basilisk FSW).
    Directs the attitude control system to re-orient the Star Tracker towards
    the incoming debris vector ahead of TCA.
    """
    command: str = "SLEW_TO_TARGET"
    target_asset: str = ""
    execute_at_utc: str = ""
    sensor_mode: str = "OPTICAL_TRACKING"

    def validate(self) -> None:
        if self.command != "SLEW_TO_TARGET":
            raise PacketValidationError(f"Invalid command '{self.command}'. Expected 'SLEW_TO_TARGET'.")
        if not self.target_asset or not isinstance(self.target_asset, str):
            raise PacketValidationError("SlewCommand requires non-empty 'target_asset' string.")
        if not self.execute_at_utc or not isinstance(self.execute_at_utc, str):
            raise PacketValidationError("SlewCommand requires non-empty 'execute_at_utc' ISO 8601 string.")
        if self.sensor_mode != "OPTICAL_TRACKING":
            raise PacketValidationError(
                f"Invalid sensor_mode '{self.sensor_mode}'. Expected 'OPTICAL_TRACKING'."
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SlewCommand":
        if not isinstance(data, dict):
            raise PacketValidationError(f"Expected JSON object dict, got {type(data).__name__}.")
        cmd = cls(
            command=str(data.get("command", "")),
            target_asset=str(data.get("target_asset", "")),
            execute_at_utc=str(data.get("execute_at_utc", "")),
            sensor_mode=str(data.get("sensor_mode", "")),
        )
        cmd.validate()
        return cmd

    @classmethod
    def from_json(cls, json_str: str) -> "SlewCommand":
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as err:
            raise PacketValidationError(f"Malformed JSON: {err}") from err
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "command": self.command,
            "target_asset": self.target_asset,
            "execute_at_utc": self.execute_at_utc,
            "sensor_mode": self.sensor_mode,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_wire(self) -> bytes:
        """Returns newline-delimited wire bytes for TCP transmission."""
        return (json.dumps(self.to_dict()) + "\n").encode("utf-8")
