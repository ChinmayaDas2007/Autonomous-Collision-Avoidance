"""
Project Kessler - Edge Processing Payload
Phase 4: The Go/No-Go Decision Engine & Active-Active Avoidance Protocol
-------------------------------------------------------------------------------
Evaluates whether an avoidance maneuver is strictly authorized based on:
1. Phase 3 Danger Corridor trajectory intersection (Burn Authorization).
2. Spacecraft vs Spacecraft multi-variable scoring matrix S (Active-Active Negotiation):
   S = w1 * Cost_lifetime + w2 * Cost_delta_v + w3 * Cost_priority + w4 * Cost_downtime
   (The satellite with the lower score executes the burn).
3. Spacecraft vs Passive Debris handling (active asset executes burn).
4. Calculation of minimal required Delta-V vector and optimal burn epoch.
"""

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from edge_pro.config import PayloadConfig
from edge_pro.models import (
    CorridorAssessment,
    ManeuverDecisionPacket,
    RefinedCDM,
    SpacecraftCapability,
)
from edge_pro.scheduler import format_iso8601_utc, parse_iso8601_utc

logger = logging.getLogger("edge_pro.decision")

# Baseline normalization constants for scoring functions
BASELINE_DELTA_V_MPS = 150.0       # Max reference remaining fuel reserve
BASELINE_LIFETIME_YEARS = 10.0     # Max reference design life
BASELINE_DOWNTIME_SEC = 600.0      # Max reference recovery downtime (10 minutes)

PRIORITY_COST_MAP: Dict[str, float] = {
    "NATIONAL_DEFENSE": 1.00,
    "PRIMARY_SCIENCE": 0.75,
    "COMMERCIAL": 0.35,
    "SECONDARY": 0.10,
}


class ActiveActiveDecisionEngine:
    """
    Autonomous Go/No-Go Decision Engine and Active-Active Avoidance Protocol.
    """

    def __init__(self, config: Optional[PayloadConfig] = None):
        self.config = config or PayloadConfig()

        # Extract weights from config and normalize to sum to 1.0
        raw_weights = {
            "w_lifetime": self.config.weight_lifetime,
            "w_delta_v": self.config.weight_delta_v,
            "w_priority": self.config.weight_priority,
            "w_downtime": self.config.weight_downtime,
        }
        total_w = sum(raw_weights.values()) or 1.0
        self.weights = {k: v / total_w for k, v in raw_weights.items()}

        # Build local asset capability from config
        self.own_capability = SpacecraftCapability(
            asset_id=self.config.own_asset_id,
            remaining_delta_v_mps=self.config.own_remaining_delta_v_mps,
            remaining_lifetime_years=self.config.own_remaining_lifetime_years,
            mission_priority=self.config.own_mission_priority,
            recovery_latency_sec=self.config.own_recovery_latency_sec,
            is_maneuverable=True,
        )

    def calculate_cost_factors(self, sat: SpacecraftCapability) -> Dict[str, float]:
        """
        Computes the four normalized cost metrics [0.0 - 1.0] for a spacecraft.
        Rule: Lower cost makes the asset more suitable to execute the maneuver.
        """
        # 1. Delta-V Reserves: Greater remaining fuel -> lower cost
        dv_ratio = max(0.0, min(1.0, sat.remaining_delta_v_mps / BASELINE_DELTA_V_MPS))
        cost_delta_v = 1.0 - dv_ratio

        # 2. Remaining Lifetime: Longer lifetime margin -> lower cost (near EOL preserves fuel)
        life_ratio = max(0.0, min(1.0, sat.remaining_lifetime_years / BASELINE_LIFETIME_YEARS))
        cost_lifetime = 1.0 - life_ratio

        # 3. Mission Priority: Higher priority -> higher cost (protects critical assets)
        prio_key = sat.mission_priority.upper().strip()
        cost_priority = PRIORITY_COST_MAP.get(prio_key, 0.50)

        # 4. Operational Downtime / Recovery: Shorter downtime -> lower cost
        down_ratio = max(0.0, min(1.0, sat.recovery_latency_sec / BASELINE_DOWNTIME_SEC))
        cost_downtime = down_ratio

        return {
            "cost_lifetime": round(cost_lifetime, 4),
            "cost_delta_v": round(cost_delta_v, 4),
            "cost_priority": round(cost_priority, 4),
            "cost_downtime": round(cost_downtime, 4),
        }

    def compute_responsibility_score(self, sat: SpacecraftCapability) -> Tuple[float, Dict[str, float]]:
        """
        Calculates Maneuver Responsibility Score S:
        S = w1 * Cost_lifetime + w2 * Cost_delta_v + w3 * Cost_priority + w4 * Cost_downtime
        """
        costs = self.calculate_cost_factors(sat)
        score = (
            self.weights["w_lifetime"] * costs["cost_lifetime"]
            + self.weights["w_delta_v"] * costs["cost_delta_v"]
            + self.weights["w_priority"] * costs["cost_priority"]
            + self.weights["w_downtime"] * costs["cost_downtime"]
        )
        return round(score, 5), costs

    def compute_minimal_avoidance_delta_v(
        self,
        corridor: CorridorAssessment,
    ) -> Tuple[List[float], float]:
        """
        Calculates the minimum required Delta-V avoidance impulse vector [m/s].
        Directs impulse perpendicular to the threat approach vector to maximize
        miss distance stand-off margin at closest approach.
        """
        magnitude = self.config.default_avoidance_delta_v_mps

        # If normalized threat vector is provided, compute an orthogonal vector
        threat = corridor.threat_vector_normalized or [1.0, 0.0, 0.0]
        tx, ty, tz = threat[0], threat[1], threat[2]

        # Use cross product with reference vector [0, 0, 1] or [0, 1, 0] to find perpendicular
        ref = [0.0, 0.0, 1.0] if abs(tz) < 0.9 else [0.0, 1.0, 0.0]
        # cross(threat, ref)
        cx = ty * ref[2] - tz * ref[1]
        cy = tz * ref[0] - tx * ref[2]
        cz = tx * ref[1] - ty * ref[0]
        norm = math.sqrt(cx * cx + cy * cy + cz * cz)
        if norm < 1e-6:
            cx, cy, cz = 0.0, 1.0, 0.0
            norm = 1.0

        # Scale to required Delta-V magnitude
        ux = (cx / norm) * magnitude
        uy = (cy / norm) * magnitude
        uz = (cz / norm) * magnitude

        return [round(ux, 5), round(uy, 5), round(uz, 5)], round(magnitude, 4)

    def calculate_optimal_burn_epoch(
        self,
        tca_utc_str: str,
        lead_time_minutes: float = 45.0,
    ) -> str:
        """
        Calculates the optimal burn epoch at an orbital node prior to TCA.
        Standard LEO minimum-energy collision avoidance burn occurs ~half an orbit (45 min)
        prior to conjunction.
        """
        tca_dt = parse_iso8601_utc(tca_utc_str)
        burn_dt = tca_dt - timedelta(minutes=lead_time_minutes)

        # Ensure burn epoch is not in the past relative to now
        now = datetime.now(timezone.utc)
        if burn_dt < now:
            burn_dt = now + timedelta(seconds=15)

        return format_iso8601_utc(burn_dt)

    def evaluate_decision(
        self,
        cdm: RefinedCDM,
        corridor: CorridorAssessment,
        peer_capability: Optional[SpacecraftCapability] = None,
    ) -> ManeuverDecisionPacket:
        """
        Executes the Phase 4 Decision Engine:
        1. Checks corridor intersection. If False -> NO_MANEUVER_REQUIRED.
        2. If True, determines whether target is Debris or Active Spacecraft.
        3. If Active Spacecraft, computes S_self vs S_peer to resolve right-of-way.
        4. Generates signed ManeuverDecisionPacket with Delta-V vector and audit trail.
        """
        now_str = format_iso8601_utc(datetime.now(timezone.utc))
        target = corridor.target_asset

        # Case 1: Optical frame differencing / corridor projection proves threat misses safety box
        if not corridor.corridor_intersected:
            logger.info(
                f"[Phase 4 Decision] Threat '{target}' cleared danger corridor "
                f"(Predicted miss: {corridor.miss_distance_m:.1f} m). Authorizing NO_MANEUVER_REQUIRED."
            )
            return ManeuverDecisionPacket(
                decision="NO_MANEUVER_REQUIRED",
                decision_status="NO_GO",
                responsibility="NONE",
                corridor_breached=False,
                target_asset=target,
                timestamp_utc=now_str,
                rationale=(
                    f"Optical streak tracking confirms debris trajectory misses 3D safety corridor "
                    f"by {corridor.miss_distance_m:.1f} m. False positive radar alarm eliminated. "
                    f"Conserving fuel and maintaining nominal mission orbit."
                ),
            )

        # Case 2: Corridor breached -> Conjunction confirmed
        logger.warning(
            f"[Phase 4 Decision] DANGER CORRIDOR BREACH CONFIRMED for target '{target}' "
            f"(Miss distance: {corridor.miss_distance_m:.1f} m < safety volume threshold)."
        )

        # Compute self responsibility score
        self_score, self_costs = self.compute_responsibility_score(self.own_capability)

        # Active-Active Negotiation vs Passive Debris determination
        is_active_peer = (
            peer_capability is not None and peer_capability.is_maneuverable
        )

        burn_epoch = self.calculate_optimal_burn_epoch(
            cdm.conjunction_data.time_of_closest_approach
        )
        dv_vec, dv_mag = self.compute_minimal_avoidance_delta_v(corridor)

        if not is_active_peer:
            # Spacecraft vs Passive Debris: Passive debris cannot maneuver; active asset assumes responsibility
            logger.info(
                f"[Phase 4 Decision] Conjunction is Spacecraft vs Inactive Debris ('{target}'). "
                f"Local satellite '{self.own_capability.asset_id}' assumes 100% maneuver responsibility."
            )
            scoring_audit = {
                "conjunction_type": "SPACECRAFT_VS_DEBRIS",
                "local_asset": self.own_capability.asset_id,
                "local_score": self_score,
                "local_factors": self_costs,
                "weights": self.weights,
            }
            return ManeuverDecisionPacket(
                decision="EXECUTE_BURN",
                decision_status="GO",
                responsibility="self",
                corridor_breached=True,
                target_asset=target,
                timestamp_utc=now_str,
                delta_v_vector_mps=dv_vec,
                delta_v_magnitude_mps=dv_mag,
                burn_epoch_utc=burn_epoch,
                scoring_audit=scoring_audit,
                rationale=(
                    f"Uncooperative debris '{target}' on direct collision course. "
                    f"Executing autonomous avoidance burn: Delta-V = {dv_mag:.3f} m/s."
                ),
            )

        # Spacecraft vs Spacecraft (Active-Active Avoidance Protocol)
        peer_score, peer_costs = self.compute_responsibility_score(peer_capability)
        logger.info(
            f"[Phase 4 Decision] Evaluating Active-Active Negotiation Matrix: "
            f"Self ({self.own_capability.asset_id}) Score S = {self_score:.4f} | "
            f"Peer ({peer_capability.asset_id}) Score S = {peer_score:.4f}"
        )

        scoring_audit = {
            "conjunction_type": "ACTIVE_ACTIVE_SPACECRAFT",
            "weights": self.weights,
            "self": {
                "asset_id": self.own_capability.asset_id,
                "score": self_score,
                "factors": self_costs,
            },
            "peer": {
                "asset_id": peer_capability.asset_id,
                "score": peer_score,
                "factors": peer_costs,
            },
        }

        # The satellite with the lower score executes the burn
        if self_score <= peer_score:
            logger.info(
                f"[Phase 4 Decision] Local satellite '{self.own_capability.asset_id}' won responsibility "
                f"(Lower score: {self_score:.4f} <= {peer_score:.4f}). Firing avoidance maneuver."
            )
            return ManeuverDecisionPacket(
                decision="EXECUTE_BURN",
                decision_status="GO",
                responsibility="self",
                corridor_breached=True,
                target_asset=target,
                timestamp_utc=now_str,
                delta_v_vector_mps=dv_vec,
                delta_v_magnitude_mps=dv_mag,
                burn_epoch_utc=burn_epoch,
                scoring_audit=scoring_audit,
                rationale=(
                    f"Active-Active decision matrix resolved responsibility to local satellite "
                    f"(Score: {self_score:.4f} vs Peer: {peer_score:.4f}). "
                    f"Local satellite has greater maneuver capacity / lower mission impact."
                ),
            )
        else:
            logger.info(
                f"[Phase 4 Decision] Peer satellite '{peer_capability.asset_id}' assigned responsibility "
                f"(Lower score: {peer_score:.4f} < {self_score:.4f}). Monitoring peer maneuver."
            )
            return ManeuverDecisionPacket(
                decision="MONITOR_PEER_MANEUVER",
                decision_status="NO_GO",
                responsibility=peer_capability.asset_id,
                corridor_breached=True,
                target_asset=target,
                timestamp_utc=now_str,
                scoring_audit=scoring_audit,
                rationale=(
                    f"Active-Active negotiation determined peer satellite '{peer_capability.asset_id}' "
                    f"will execute the avoidance maneuver (Peer Score {peer_score:.4f} < Self Score {self_score:.4f}). "
                    f"Preserving local fuel and standing by to confirm range clearance."
                ),
            )
