"""
Project Kessler — Ground AI Node (AOID)
-------------------------------------------------------------------------------
Ground Operations intelligence desk implementation providing:
1. Phase 1: SGP4 Ephemeris propagation of primary satellite and debris to TCA.
2. Phase 2: Multi-orbit backstep (obs_start = TCA - 2*T) using orbital periodicity.
3. Phase 3: Solar umbra eclipse check using Skyfield or analytical solar ephemeris.
4. Phase 4: Enforced Refined CDM JSON output contract with ML atmospheric drag.
5. Dual-transport publisher: atomic file write, broadcast TCP server, and edge uplink client.
"""

import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from shared.ws_broadcast import DashboardBroadcaster

import requests

# ── Optional: SGP4 ────────────────────────────────────────────────────────────
try:
    from sgp4.api import Satrec, WGS84
    SGP4_AVAILABLE = True
except ImportError:
    SGP4_AVAILABLE = False

# ── Optional: Skyfield ────────────────────────────────────────────────────────
try:
    from skyfield.api import load as sf_load
    SKYFIELD_AVAILABLE = True
except ImportError:
    SKYFIELD_AVAILABLE = False

# ── Optional: XGBoost ─────────────────────────────────────────────────────────
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ground_ai_node")


# Project root directory detection:
_FILE_PATH = Path(__file__).resolve()
WORKSPACE_ROOT = _FILE_PATH.parent.parent if _FILE_PATH.parent.name == "gnd_ops" else _FILE_PATH.parent

# ── Configuration Defaults ───────────────────────────────────────────────────
CONFIG = {
    # NOAA space-weather endpoints (live)
    "noaa_f107_url": "https://services.swpc.noaa.gov/products/summary/10cm-flux.json",
    "noaa_kp_url":   "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
    "poll_interval_sec": 60,
    "http_timeout_sec":  5,
    # File transport
    "shared_dir":      WORKSPACE_ROOT / "shared",
    "cdm_output_path": WORKSPACE_ROOT / "shared" / "cdm_packet.json",
    "initial_ephemeris_path": WORKSPACE_ROOT / "shared" / "initial_ephemeris.json",
    # TCP broadcast server (for local dashboard/debug monitors)
    "tcp_broadcast_host": "127.0.0.1",
    "tcp_broadcast_port": 8765,
    # TCP uplink client target (edge_pro TCPIngestServer)
    "edge_uplink_host": "127.0.0.1",
    "edge_uplink_port": 5555,
    # XGBoost model path
    "model_path": WORKSPACE_ROOT / "models" / "drag_xgb_model.json",
    # Conservative quiet-sun defaults
    "default_f107": 150.0,
    "default_kp":   3.0,
    # Ballistic coefficient defaults (kg/m²)
    "default_bc_satellite": 100.0,
    "default_bc_debris":     30.0,
    # Action decision threshold
    "optical_confirmation_multiplier_threshold": 1.15,
    # Skyfield ephemeris cache dir
    "skyfield_data_dir": WORKSPACE_ROOT / "skyfield_data",
    # Earth radius for cylindrical umbra approximation (km)
    "earth_radius_km": 6371.0,
    # Action enum preference: "RECOMMEND_OPTICAL_CONFIRMATION" or "PROCEED_OPTICAL_TRACK"
    "sunlit_action_enum": "RECOMMEND_OPTICAL_CONFIRMATION",
}
CONFIG["shared_dir"].mkdir(parents=True, exist_ok=True)
CONFIG["skyfield_data_dir"].mkdir(parents=True, exist_ok=True)


# ── Analytical Solar Ephemeris Fallback ─────────────────────────────────────────
def calculate_sun_position_eci(dt: datetime) -> Tuple[List[float], List[float]]:
    """
    Computes Sun ECI position (km) and unit direction vector using the standard
    Astronomical Almanac / Vallado analytical solar algorithm.
    Provides sub-arcminute accuracy without external library or ephemeris file downloads.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    y, mo, d = dt.year, dt.month, dt.day
    jd = (
        367 * y
        - int(7 * (y + int((mo + 9) / 12)) / 4)
        + int(275 * mo / 9)
        + d
        + 1721013.5
    )
    fr = (
        dt.hour * 3600.0
        + dt.minute * 60.0
        + dt.second
        + dt.microsecond / 1e6
    ) / 86400.0
    jd_total = jd + fr
    n = jd_total - 2451545.0

    # Mean longitude of the Sun (deg)
    L = (280.460 + 0.9856474 * n) % 360.0
    # Mean anomaly of the Sun (deg)
    g = (357.528 + 0.9856003 * n) % 360.0
    g_rad = math.radians(g)

    # Ecliptic longitude of the Sun (deg)
    lam = (L + 1.915 * math.sin(g_rad) + 0.020 * math.sin(2.0 * g_rad)) % 360.0
    lam_rad = math.radians(lam)

    # Obliquity of the ecliptic (deg)
    eps = 23.439 - 0.0000004 * n
    eps_rad = math.radians(eps)

    # Distance in AU
    r_au = 1.00014 - 0.01671 * math.cos(g_rad) - 0.00014 * math.cos(2.0 * g_rad)
    r_km = r_au * 149597870.7

    ux = math.cos(lam_rad)
    uy = math.sin(lam_rad) * math.cos(eps_rad)
    uz = math.sin(lam_rad) * math.sin(eps_rad)

    return [ux * r_km, uy * r_km, uz * r_km], [ux, uy, uz]


# ── Ephemeris & Observation Scheduling (Phases 1, 2, 3) ────────────────────────
class EphemerisProcessor:
    """
    Handles orbital mechanics preprocessing:
      Phase 1: SGP4 propagation to obtain ECI state vectors at TCA.
      Phase 2: Multi-orbit backstep (TCA - 2*T) using TLE mean motion.
      Phase 3: Solar umbra eclipse verification at obs_start.
    """

    # Baseline TLE for primary satellite (Kessler_Sat_1, ~400 km LEO)
    DEFAULT_PRIMARY_TLE = (
        "1 25544U 98067A   26250.54791667  .00002182  00000-0  48260-4 0  9991",
        "2 25544  51.6416  49.1234 0005770  81.2000 278.9700 15.50377932123456",
    )
    # Baseline TLE for secondary debris object
    DEFAULT_DEBRIS_TLE = (
        "1 45678U 20001A   26250.54791667  .00001500  00000-0  35000-4 0  9991",
        "2 45678  51.6500  50.0000 0012000  85.0000 275.0000 15.48000000 98765",
    )

    def __init__(self, config: dict):
        self.config = config
        self._ts = None
        self._eph = None

    def _load_skyfield(self):
        """Lazy-loads Skyfield timescale + DE421 ephemeris if available."""
        if not SKYFIELD_AVAILABLE:
            return None, None
        if self._ts is not None:
            return self._ts, self._eph
        try:
            self._ts = sf_load.timescale()
            self._eph = sf_load("de421.bsp")
        except Exception:
            try:
                from skyfield.api import Loader
                ldr = Loader(str(self.config["skyfield_data_dir"]))
                self._ts = ldr.timescale()
                self._eph = ldr("de421.bsp")
            except Exception as e:
                logger.warning("Skyfield initialization failed (%s); using analytical solar ephemeris.", e)
                return None, None
        return self._ts, self._eph

    @staticmethod
    def _datetime_to_jd(dt: datetime) -> Tuple[float, float]:
        """Converts UTC datetime to (jd_whole, fraction) for SGP4."""
        y, mo, d = dt.year, dt.month, dt.day
        jd = (
            367 * y
            - int(7 * (y + int((mo + 9) / 12)) / 4)
            + int(275 * mo / 9)
            + d
            + 1721013.5
        )
        fr = (
            dt.hour * 3600.0
            + dt.minute * 60.0
            + dt.second
            + dt.microsecond / 1e6
        ) / 86400.0
        return jd, fr

    def propagate_satellite(
        self, dt: datetime, tle: Tuple[str, str], fallback_pos: List[float] = None
    ) -> Tuple[List[float], int]:
        """Propagates a satellite to a specific datetime via SGP4."""
        fallback = fallback_pos or [6771.0, 0.0, 0.0]
        if not SGP4_AVAILABLE:
            return fallback, -1
        try:
            sat = Satrec.twoline2rv(tle[0], tle[1])
            jd, fr = self._datetime_to_jd(dt)
            e, r, _ = sat.sgp4(jd, fr)
            if e == 0:
                return [round(c, 4) for c in r], 0
            logger.warning("SGP4 propagation error code %d; using fallback.", e)
            return fallback, e
        except Exception as ex:
            logger.warning("SGP4 propagation exception (%s); using fallback.", ex)
            return fallback, -1

    # ── Phase 1: Ephemeris & State Vector Initialization ───────────────────────
    def propagate_to_tca(
        self,
        tca_dt: datetime,
        primary_tle: Tuple[str, str] = None,
        debris_tle: Tuple[str, str] = None,
    ) -> dict:
        """
        Ingests raw TLEs for both primary satellite and debris object.
        Propagates both to nominal TCA to establish baseline (X, Y, Z) state vectors.
        """
        p_tle = primary_tle or self.DEFAULT_PRIMARY_TLE
        d_tle = debris_tle or self.DEFAULT_DEBRIS_TLE

        if not SGP4_AVAILABLE:
            logger.warning("sgp4 library not installed — using placeholder state vectors")
            return {
                "primary_eci_km": [6771.0, 0.0, 0.0],
                "debris_eci_km":  [6772.2, 0.0, 0.0],
                "primary_mean_motion_rad_per_min": 2.0 * math.pi / 92.4,
                "sgp4_used": False,
            }

        sat_p = Satrec.twoline2rv(p_tle[0], p_tle[1])
        sat_d = Satrec.twoline2rv(d_tle[0], d_tle[1])

        jd, fr = self._datetime_to_jd(tca_dt)
        e1, r1, _ = sat_p.sgp4(jd, fr)
        e2, r2, _ = sat_d.sgp4(jd, fr)

        p_eci = [round(c, 4) for c in r1] if e1 == 0 else [6771.0, 0.0, 0.0]
        d_eci = [round(c, 4) for c in r2] if e2 == 0 else [6772.2, 0.0, 0.0]

        logger.info(
            "Phase 1 | SGP4 TCA Propagation | primary_eci=%s km  debris_eci=%s km  n0=%.6f rad/min",
            p_eci, d_eci, sat_p.no_kozai,
        )
        return {
            "primary_eci_km": p_eci,
            "debris_eci_km":  d_eci,
            "primary_mean_motion_rad_per_min": sat_p.no_kozai,
            "sgp4_used": True,
        }

    def export_initial_ephemeris(
        self,
        t_init_dt: datetime,
        tca_dt: datetime,
        primary_tle: Tuple[str, str] = None,
        debris_tle: Tuple[str, str] = None,
        primary_name: str = "Primary_Sat",
        debris_name: str = "Debris_Obj",
    ) -> dict:
        """
        Master Ephemeris Authority: Computes exact Cartesian (X, Y, Z, Vx, Vy, Vz)
        state vectors in SI units (meters, m/s) at simulation epoch t_init and TCA via SGP4.
        Guarantees that core_phy initializes from the exact same orbital universe.
        """
        p_tle = primary_tle or self.DEFAULT_PRIMARY_TLE
        d_tle = debris_tle or self.DEFAULT_DEBRIS_TLE

        if not SGP4_AVAILABLE:
            p_r_m = [-5670970.0, -2422440.0, 2839460.0]
            p_v_m = [2674.1, -6844.2, -542.8]
            d_r_m = [-5587345.8, -1651084.9, 3489994.1]
            d_v_m = [2801.3, -6722.1, -610.4]
        else:
            sat_p = Satrec.twoline2rv(p_tle[0], p_tle[1])
            sat_d = Satrec.twoline2rv(d_tle[0], d_tle[1])

            jd_init, fr_init = self._datetime_to_jd(t_init_dt)
            e1, r1, v1 = sat_p.sgp4(jd_init, fr_init)
            e2, r2, v2 = sat_d.sgp4(jd_init, fr_init)

            p_r_m = [round(c * 1000.0, 2) for c in r1] if e1 == 0 else [-5670970.0, -2422440.0, 2839460.0]
            p_v_m = [round(c * 1000.0, 3) for c in v1] if e1 == 0 else [2674.1, -6844.2, -542.8]
            d_r_m = [round(c * 1000.0, 2) for c in r2] if e2 == 0 else [-5587345.8, -1651084.9, 3489994.1]
            d_v_m = [round(c * 1000.0, 3) for c in v2] if e2 == 0 else [2801.3, -6722.1, -610.4]

        return {
            "header": {
                "type": "INITIAL_EPHEMERIS",
                "authority": "Ground_AI_Node",
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "epoch": {
                "t_init_utc": t_init_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tca_utc": tca_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "primary_asset": {
                "name": primary_name,
                "mass_kg": 750.0,
                "r_eci_m": p_r_m,
                "v_eci_m_s": p_v_m,
            },
            "debris_asset": {
                "name": debris_name,
                "mass_kg": 100.0,
                "r_eci_m": d_r_m,
                "v_eci_m_s": d_v_m,
            },
        }

    # ── Phase 2: Multi-Orbit Backstep ──────────────────────────────────────────
    def compute_observation_window(
        self,
        tca_dt: datetime,
        mean_motion_rad_per_min: float,
    ) -> dict:
        """
        Extracts mean motion n0 and computes orbital period T = 2*pi / n0 minutes.
        Observation window start: obs_start = TCA - (2 * T).
        """
        period_min = round(2.0 * math.pi / mean_motion_rad_per_min, 4)
        backstep_min = round(2.0 * period_min, 4)
        obs_start = tca_dt - timedelta(minutes=backstep_min)

        logger.info(
            "Phase 2 | Multi-Orbit Backstep | T=%.2f min  backstep=%.2f min (2.0 revs)  obs_start=%s",
            period_min, backstep_min, obs_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return {
            "orbital_period_min": period_min,
            "backstep_min":       backstep_min,
            "obs_start_utc":      obs_start,
        }

    # ── Phase 3: Solar Umbra / Eclipse Verification ────────────────────────────
    def check_umbra(
        self,
        obs_start_utc: datetime,
        satellite_eci_km: List[float],
    ) -> dict:
        """
        Verifies whether primary satellite is in direct sunlight or Earth umbra (shadow)
        at obs_start_utc using a cylindrical Earth shadow model:
            sun_hat  = Sun_ECI / |Sun_ECI|
            proj_km  = dot(sat_eci, sun_hat)   (positive = daylight side)
            perp_km  = |sat_eci - proj * sun_hat|
            in_umbra = (proj_km < 0) AND (perp_km < R_Earth)
        """
        if obs_start_utc.tzinfo is None:
            obs_start_utc = obs_start_utc.replace(tzinfo=timezone.utc)

        sun_km, sun_hat = None, None
        skyfield_used = False

        # Attempt Skyfield high-precision ephemeris first
        if SKYFIELD_AVAILABLE:
            try:
                ts, eph = self._load_skyfield()
                if ts is not None and eph is not None:
                    t = ts.from_datetime(obs_start_utc)
                    sun_vec = (eph["sun"] - eph["earth"]).at(t).position.km
                    norm = math.sqrt(sum(float(c)**2 for c in sun_vec))
                    sun_km = [round(float(c), 2) for c in sun_vec]
                    sun_hat = [float(c) / norm for c in sun_vec]
                    skyfield_used = True
            except Exception as e:
                logger.warning("Skyfield query failed (%s); falling back to analytical solar ephemeris.", e)

        # Autonomous Analytical Solar Ephemeris fallback
        if sun_hat is None:
            sun_km, sun_hat = calculate_sun_position_eci(obs_start_utc)
            sun_km = [round(c, 2) for c in sun_km]

        sat = satellite_eci_km
        proj_km = sum(sat[i] * sun_hat[i] for i in range(3))
        perp_vec = [sat[i] - proj_km * sun_hat[i] for i in range(3)]
        perp_km = math.sqrt(sum(c**2 for c in perp_vec))

        r_earth = self.config["earth_radius_km"]
        in_umbra = (proj_km < 0.0) and (perp_km < r_earth)

        logger.info(
            "Phase 3 | Umbra Check | proj=%.1f km  perp=%.1f km  R_earth=%.1f km  in_umbra=%s (skyfield=%s)",
            proj_km, perp_km, r_earth, in_umbra, skyfield_used,
        )
        return {
            "is_in_umbra":   in_umbra,
            "skyfield_used": skyfield_used,
            "sun_eci_km":    sun_km,
            "perp_dist_km":  round(perp_km, 2),
            "proj_km":       round(proj_km, 2),
        }

    # ── Pipeline Orchestration (Phases 1-3) ────────────────────────────────────
    def run(
        self,
        tca_dt: datetime,
        primary_tle: Tuple[str, str] = None,
        debris_tle: Tuple[str, str] = None,
        simulate_eclipse: bool = False,
        force_sunlit: bool = False,
    ) -> dict:
        """
        Executes complete ephemeris preprocessing:
          1. Propagate both objects to TCA.
          2. Compute 2-orbit backstep window obs_start_utc.
          3. Propagate primary satellite to obs_start_utc for precise shadow evaluation.
          4. Execute cylindrical umbra check.
        """
        p_tle = primary_tle or self.DEFAULT_PRIMARY_TLE
        d_tle = debris_tle or self.DEFAULT_DEBRIS_TLE

        ephem = self.propagate_to_tca(tca_dt, p_tle, d_tle)
        obs_win = self.compute_observation_window(
            tca_dt, ephem["primary_mean_motion_rad_per_min"]
        )

        # Propagate primary satellite directly to obs_start_utc for true shadow geometry
        sat_obs_eci, _ = self.propagate_satellite(
            obs_win["obs_start_utc"], p_tle, fallback_pos=ephem["primary_eci_km"]
        )

        umbra = self.check_umbra(obs_win["obs_start_utc"], sat_obs_eci)
        if simulate_eclipse:
            logger.warning("[SIMULATION OVERRIDE] Artificially forcing is_in_umbra=True for eclipse testing.")
            umbra["is_in_umbra"] = True
        elif force_sunlit:
            logger.info("[SIMULATION OVERRIDE] Forcing is_in_umbra=False for sunlit optical tracking testing.")
            umbra["is_in_umbra"] = False

        return {
            "primary_eci_km":      ephem["primary_eci_km"],
            "debris_eci_km":       ephem["debris_eci_km"],
            "primary_obs_eci_km":  sat_obs_eci,
            "mean_motion_rad_min": ephem["primary_mean_motion_rad_per_min"],
            "sgp4_used":           ephem["sgp4_used"],
            "orbital_period_min":  obs_win["orbital_period_min"],
            "obs_start_utc":       obs_win["obs_start_utc"],
            **umbra,
        }


# ── Space Weather Data Ingestion ───────────────────────────────────────────────
class DataIngestor:
    """Fetches real-time space weather (F10.7 and Kp) from NOAA SWPC."""

    def __init__(self, config: dict):
        self.config = config
        self._cache = {"f107": None, "kp": None}
        self._stale = False

    def fetch_f107(self) -> float:
        try:
            resp = requests.get(
                self.config["noaa_f107_url"],
                timeout=self.config["http_timeout_sec"],
            )
            resp.raise_for_status()
            data = resp.json()
            latest = data[-1] if isinstance(data, list) else data
            value = float(latest.get("flux", latest.get("f107", latest.get("10cm-flux"))))
            self._cache["f107"] = value
            return value
        except Exception as exc:
            logger.warning("F10.7 fetch failed (%s); using fallback.", exc)
            return self._fallback("f107")

    def fetch_kp(self) -> float:
        try:
            resp = requests.get(
                self.config["noaa_kp_url"],
                timeout=self.config["http_timeout_sec"],
            )
            resp.raise_for_status()
            data = resp.json()
            latest = data[-1] if isinstance(data, list) else data
            raw = latest.get("estimated_kp")
            value = float(raw) if raw is not None else float(latest["kp_index"])
            self._cache["kp"] = value
            return value
        except Exception as exc:
            logger.warning("Kp fetch failed (%s); using fallback.", exc)
            return self._fallback("kp")

    def _fallback(self, key: str) -> float:
        self._stale = True
        if self._cache[key] is not None:
            logger.info("Using cached %s = %s (stale_data=True)", key, self._cache[key])
            return self._cache[key]
        default = self.config[f"default_{key}"]
        logger.info("No cache for %s; using quiet-sun default = %s", key, default)
        return default

    def poll(self) -> dict:
        self._stale = False
        f107 = self.fetch_f107()
        kp = self.fetch_kp()
        return {"f107": f107, "kp": kp, "stale_data": self._stale}


# ── Feature Engineering ───────────────────────────────────────────────────────
class FeatureBuilder:
    """Assembles the 4-feature vector: [f107, kp, BC_sat, BC_debris]."""

    def __init__(self, config: dict):
        self.config = config

    def build(self, weather: dict, conjunction: dict) -> dict:
        bc_sat = conjunction.get("bc_satellite")
        bc_debris = conjunction.get("bc_debris")
        bc_defaulted = False

        if bc_sat is None:
            bc_sat = self.config["default_bc_satellite"]
            bc_defaulted = True
        if bc_debris is None:
            bc_debris = self.config["default_bc_debris"]
            bc_defaulted = True

        return {
            "f107":         weather["f107"],
            "kp":           weather["kp"],
            "bc_satellite": float(bc_sat),
            "bc_debris":    float(bc_debris),
            "bc_defaulted": bc_defaulted,
            "stale_data":   weather.get("stale_data", False),
        }

    @staticmethod
    def to_vector(features: dict) -> List[float]:
        return [
            features["f107"],
            features["kp"],
            features["bc_satellite"],
            features["bc_debris"],
        ]


# ── ML Atmospheric Drag Prediction ─────────────────────────────────────────────
class DragPredictor:
    """Lightweight XGBoost regressor with physics-motivated fallback heuristic."""

    def __init__(self, config: dict):
        self.config = config
        self.model = None
        if XGBOOST_AVAILABLE and config["model_path"].exists():
            try:
                self.model = xgb.XGBRegressor()
                self.model.load_model(str(config["model_path"]))
                logger.info("Loaded XGBoost model from %s", config["model_path"])
            except Exception as exc:
                logger.warning("Model load failed (%s); using _mock_inference heuristic.", exc)
                self.model = None
        else:
            logger.info("No trained model found — using _mock_inference heuristic.")

    def _mock_inference(self, features: dict) -> Tuple[float, List[float]]:
        f107 = features["f107"]
        kp = features["kp"]
        bc_debris = features["bc_debris"]

        f107_term = (f107 - 150.0) / 150.0
        kp_term = (kp - 3.0) / 9.0

        dm = 1.0 + 0.6 * f107_term + 0.4 * kp_term
        dm = max(0.5, min(3.0, dm))

        bc_sensitivity = 30.0 / max(bc_debris, 1.0)
        base_sigma = 50.0 * bc_sensitivity * dm

        return dm, [
            round(base_sigma * 1.0, 2),  # sigma_Radial (m)
            round(base_sigma * 3.0, 2),  # sigma_In-track (m)
            round(base_sigma * 1.0, 2),  # sigma_Cross-track (m)
        ]

    def predict(self, features: dict) -> Tuple[float, List[float]]:
        if self.model is not None:
            try:
                vec = [FeatureBuilder.to_vector(features)]
                dm = float(self.model.predict(vec)[0])
                _, cov = self._mock_inference(features)
                return dm, cov
            except Exception as exc:
                logger.warning("Model inference failed (%s); falling back.", exc)
        return self._mock_inference(features)


# ── Refined CDM Builder (Phase 4 Contract) ────────────────────────────────────
class CDMBuilder:
    """
    Constructs the strictly-typed Refined CDM JSON packet.
    Decision Logic:
      1. is_in_umbra == True               -> ABORT_VISION_USE_GROUND_RADAR
      2. |dm - 1.0| >= threshold           -> RECOMMEND_OPTICAL_CONFIRMATION / PROCEED_OPTICAL_TRACK
      3. miss_dist <= 3 * sigma_in-track   -> RECOMMEND_OPTICAL_CONFIRMATION / PROCEED_OPTICAL_TRACK
      4. otherwise                         -> NO_ACTION
    """

    def __init__(self, config: dict):
        self.config = config

    def _decide_action(
        self,
        drag_multiplier: float,
        covariance: List[float],
        miss_distance_km: float,
        is_in_umbra: bool = False,
    ) -> str:
        if is_in_umbra:
            return "ABORT_VISION_USE_GROUND_RADAR"

        threshold = self.config["optical_confirmation_multiplier_threshold"]
        drag_triggered = abs(drag_multiplier - 1.0) >= (threshold - 1.0)

        sigma_in_track_km = covariance[1] / 1000.0
        uncertainty_triggered = miss_distance_km <= (3.0 * sigma_in_track_km)

        if drag_triggered or uncertainty_triggered:
            return self.config.get("sunlit_action_enum", "RECOMMEND_OPTICAL_CONFIRMATION")
        return "NO_ACTION"

    def build(
        self,
        conjunction: dict,
        features: dict,
        drag_multiplier: float,
        covariance: List[float],
        preprocessing: dict = None,
    ) -> dict:
        preprocessing = preprocessing or {}
        miss_dist = conjunction.get("miss_distance_km", 999.0)
        is_in_umbra = preprocessing.get("is_in_umbra", False)

        action = self._decide_action(drag_multiplier, covariance, miss_dist, is_in_umbra)

        obs_start = preprocessing.get("obs_start_utc")
        if isinstance(obs_start, datetime):
            obs_start_str = obs_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            obs_start_str = str(obs_start)

        # ── Phase 4 Enforced JSON Output Contract ──────────────────────────────
        return {
            "header": {
                "type":          "REFINED_CDM",
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "conjunction_data": {
                "primary_asset":                conjunction["primary_asset"],
                "secondary_asset":              conjunction["secondary_asset"],
                "time_of_closest_approach":     conjunction["time_of_closest_approach"],
                "observation_window_start_utc": obs_start_str,
                "miss_distance_km":             float(miss_dist),
            },
            "ai_drag_prediction": {
                "f107_flux":                   float(features["f107"]),
                "kp_index":                    float(features["kp"]),
                "drag_multiplier":             round(float(drag_multiplier), 4),
                "ellipsoid_covariance_matrix": [float(c) for c in covariance],
            },
            "action": action,
            "_audit": {
                "stale_data":         features.get("stale_data", False),
                "bc_defaulted":       features.get("bc_defaulted", False),
                "sgp4_used":          preprocessing.get("sgp4_used", False),
                "skyfield_used":      preprocessing.get("skyfield_used", False),
                "is_in_umbra":        is_in_umbra,
                "orbital_period_min": preprocessing.get("orbital_period_min"),
            },
        }


# ── Dual-Transport Publisher ──────────────────────────────────────────────────
class Publisher:
    """
    Publishes Refined CDMs via:
      1. Atomic disk file (shared/cdm_packet.json)
      2. TCP broadcast server (port 8765, for UI/dashboard clients)
      3. TCP uplink client (port 5555, connects directly to edge_pro TCPIngestServer)
    """

    def __init__(self, config: dict):
        self.config = config
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._server_thread: Optional[threading.Thread] = None
        self._server_socket: Optional[socket.socket] = None
        self._running = False

    def start_broadcast_server(self):
        """Starts local TCP broadcast server on port 8765."""
        if self._running:
            return
        self._running = True
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((
            self.config["tcp_broadcast_host"],
            self.config["tcp_broadcast_port"],
        ))
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        def accept_loop():
            logger.info(
                "Ground AI TCP broadcast server listening on %s:%s",
                self.config["tcp_broadcast_host"],
                self.config["tcp_broadcast_port"],
            )
            while self._running:
                try:
                    conn, addr = self._server_socket.accept()
                    with self._clients_lock:
                        self._clients.append(conn)
                    logger.info("Broadcast client connected: %s", addr)
                except socket.timeout:
                    continue
                except OSError:
                    break

        self._server_thread = threading.Thread(target=accept_loop, daemon=True)
        self._server_thread.start()

    def stop_broadcast_server(self):
        """Stops local TCP broadcast server."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients = []

    def publish_file(self, packet: dict, out_path: Path = None) -> Path:
        """Atomically writes JSON packet to disk."""
        target_path = out_path or self.config["cdm_output_path"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(packet, indent=2))
            os.replace(tmp_path, target_path)
            logger.info("Published Refined CDM atomically to %s", target_path)
            return target_path
        except Exception as exc:
            logger.error("Atomic file write failed (%s)", exc)
            return target_path

    def publish_initial_ephemeris(self, ephemeris: dict, out_path: Path = None) -> Path:
        """Atomically saves master SGP4 Cartesian ephemeris state vectors for core_phy."""
        target_path = out_path or self.config["initial_ephemeris_path"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(ephemeris, indent=2))
            os.replace(tmp_path, target_path)
            logger.info("Published Master Initial Ephemeris to %s", target_path)
            return target_path
        except Exception as exc:
            logger.error("Initial ephemeris file write failed (%s)", exc)
            return target_path

    def broadcast_tcp(self, packet: dict) -> int:
        """Broadcasts packet to all connected dashboard/debug clients."""
        line = (json.dumps(packet) + "\n").encode("utf-8")
        with self._clients_lock:
            alive = []
            for conn in self._clients:
                try:
                    conn.sendall(line)
                    alive.append(conn)
                except OSError:
                    try:
                        conn.close()
                    except OSError:
                        pass
            self._clients = alive
            return len(self._clients)

    def uplink_to_edge(
        self,
        packet: dict,
        host: str = None,
        port: int = None,
        timeout: float = 5.0,
    ) -> bool:
        """
        Connects as a TCP client to edge_pro TCPIngestServer (port 5555),
        transmits the Refined CDM packet, and awaits acknowledgment.
        """
        target_host = host or self.config["edge_uplink_host"]
        target_port = port or self.config["edge_uplink_port"]
        wire_bytes = (json.dumps(packet) + "\n").encode("utf-8")

        logger.info("[Uplink] Connecting to Edge Payload at %s:%s...", target_host, target_port)
        try:
            with socket.create_connection((target_host, target_port), timeout=timeout) as sock:
                sock.sendall(wire_bytes)
                logger.info("[Uplink] Transmitted Refined CDM (action: %s)", packet.get("action"))
                sock.settimeout(timeout)
                resp = sock.recv(1024)
                if resp:
                    logger.info("[Uplink] Received Edge Payload ACK: %s", resp.decode("utf-8").strip())
                return True
        except ConnectionRefusedError:
            logger.error(
                "[Uplink] Connection refused at %s:%s. Is edge_pro TCPIngestServer running?",
                target_host, target_port,
            )
            return False
        except Exception as ex:
            logger.error("[Uplink] Transmission failure (%s)", ex)
            return False

    def publish(
        self,
        packet: dict,
        out_path: Path = None,
        uplink: bool = False,
        uplink_host: str = None,
        uplink_port: int = None,
    ) -> dict:
        """Publishes packet across active transport channels."""
        self.publish_file(packet, out_path=out_path)
        if self._running:
            self.broadcast_tcp(packet)
        if uplink:
            self.uplink_to_edge(packet, host=uplink_host, port=uplink_port)
        return packet


# ── Ground AI Node Orchestrator ────────────────────────────────────────────────
class GroundAINode:
    """
    Top-level Ground AI Service coordinating:
      EphemerisProcessor -> DataIngestor -> FeatureBuilder -> DragPredictor -> CDMBuilder -> Publisher
    """

    def __init__(self, config: dict = None):
        self.config = config or dict(CONFIG)
        self.ephemeris = EphemerisProcessor(self.config)
        self.ingestor = DataIngestor(self.config)
        self.feature_builder = FeatureBuilder(self.config)
        self.predictor = DragPredictor(self.config)
        self.cdm_builder = CDMBuilder(self.config)
        self.publisher = Publisher(self.config)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.last_packet: Optional[dict] = None
        
        self.dashboard = DashboardBroadcaster(port=8003)
        self.dashboard.run_in_background()

    def run_once(
        self,
        conjunction: dict,
        out_path: Path = None,
        primary_tle: Tuple[str, str] = None,
        debris_tle: Tuple[str, str] = None,
        uplink: bool = False,
        uplink_host: str = None,
        uplink_port: int = None,
        simulate_eclipse: bool = False,
        force_sunlit: bool = False,
    ) -> dict:
        """Executes a single cycle synchronously and publishes output."""
        tca_str = conjunction["time_of_closest_approach"]
        tca_dt = datetime.fromisoformat(
            tca_str.replace("Z", "+00:00")
        ).replace(tzinfo=timezone.utc)

        preprocessing = self.ephemeris.run(
            tca_dt,
            primary_tle=primary_tle,
            debris_tle=debris_tle,
            simulate_eclipse=simulate_eclipse,
            force_sunlit=force_sunlit,
        )
        weather = self.ingestor.poll()
        features = self.feature_builder.build(weather, conjunction)
        dm, covariance = self.predictor.predict(features)

        packet = self.cdm_builder.build(
            conjunction, features, dm, covariance, preprocessing
        )

        # Master Ephemeris Authority: export Cartesian state vectors at obs_start for core_phy
        initial_ephem = self.ephemeris.export_initial_ephemeris(
            t_init_dt=preprocessing["obs_start_utc"],
            tca_dt=tca_dt,
            primary_tle=primary_tle,
            debris_tle=debris_tle,
            primary_name=conjunction.get("primary_asset", "Primary_Sat"),
            debris_name=conjunction.get("secondary_asset", "Debris_Obj"),
        )
        self.publisher.publish_initial_ephemeris(initial_ephem)

        self.publisher.publish(
            packet,
            out_path=out_path,
            uplink=uplink,
            uplink_host=uplink_host,
            uplink_port=uplink_port,
        )
        self.last_packet = packet
        
        # Broadcast to UI Dashboard
        self.dashboard.broadcast({
            "type": "drag",
            "ts": datetime.now(timezone.utc).timestamp(),
            "cd": 2.2, # Hardcoded fallback if not in feature builder
            "drag_sigma": 0.1, 
            "ellipsoid": {
                "along": packet.get("ai_drag_prediction", {}).get("ellipsoid_covariance_matrix", [0,0,0])[0],
                "cross": packet.get("ai_drag_prediction", {}).get("ellipsoid_covariance_matrix", [0,0,0])[1],
                "radial": packet.get("ai_drag_prediction", {}).get("ellipsoid_covariance_matrix", [0,0,0])[2]
            },
            "time_of_closest_approach": packet.get("conjunction_data", {}).get("time_of_closest_approach", ""),
            "miss_distance_km": packet.get("conjunction_data", {}).get("miss_distance_km", 0.0)
        })
        
        return packet

    def start(
        self,
        conjunction: dict,
        interval_sec: int = None,
        primary_tle: Tuple[str, str] = None,
        debris_tle: Tuple[str, str] = None,
        uplink: bool = False,
        enable_broadcast: bool = True,
    ):
        """Starts background polling and publishing daemon."""
        interval_sec = interval_sec or self.config["poll_interval_sec"]
        if enable_broadcast:
            self.publisher.start_broadcast_server()
        self._running = True

        def loop():
            logger.info("Ground AI Node background daemon started (interval=%ss)", interval_sec)
            while self._running:
                try:
                    pkt = self.run_once(
                        conjunction,
                        primary_tle=primary_tle,
                        debris_tle=debris_tle,
                        uplink=uplink,
                    )
                    logger.info(
                        "Cycle completed | drag=%.3f | action=%s",
                        pkt["ai_drag_prediction"]["drag_multiplier"],
                        pkt["action"],
                    )
                except Exception as exc:
                    logger.error("Daemon cycle failure (%s) — continuing", exc)
                time.sleep(interval_sec)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Gracefully terminates daemon and shuts down sockets."""
        self._running = False
        self.publisher.stop_broadcast_server()
        logger.info("Ground AI Node stopped.")


# ── Command Line Interface ─────────────────────────────────────────────────────
def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ground AI Node — Project Kessler Autonomous Operations Intelligence Desk"
    )
    parser.add_argument("--once", action="store_true", help="Run a single prediction cycle and exit.")
    parser.add_argument("--uplink", action="store_true", help="Uplink Refined CDM to edge_pro TCPIngestServer.")
    parser.add_argument("--uplink-host", default="127.0.0.1", help="Target edge payload host (default: 127.0.0.1).")
    parser.add_argument("--uplink-port", type=int, default=5555, help="Target edge payload port (default: 5555).")
    parser.add_argument("--simulate-eclipse", action="store_true", help="Simulate Earth umbra eclipse to test abort path.")
    parser.add_argument("--daemon", action="store_true", help="Run as continuous background daemon.")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60).")
    parser.add_argument("--primary-asset", default="Kessler_Sat_1", help="Primary satellite identifier.")
    parser.add_argument("--secondary-asset", default="Debris_Obj_8492", help="Debris threat identifier.")
    parser.add_argument("--tca", default=None, help="TCA in ISO 8601 format (default: now + 3 hours).")
    parser.add_argument("--miss-distance", type=float, default=0.12, help="Nominal miss distance in km (default: 0.12).")
    return parser.parse_args()


def main():
    args = parse_cli_args()

    tca_str = args.tca
    if not tca_str:
        tca_dt = datetime.now(timezone.utc) + timedelta(hours=3)
        tca_str = tca_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    conjunction = {
        "primary_asset": args.primary_asset,
        "secondary_asset": args.secondary_asset,
        "time_of_closest_approach": tca_str,
        "miss_distance_km": args.miss_distance,
    }

    node = GroundAINode()

    if args.daemon:
        node.start(
            conjunction,
            interval_sec=args.interval,
            uplink=args.uplink,
        )
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            node.stop()
    else:
        # Run once (default)
        pkt = node.run_once(
            conjunction,
            uplink=args.uplink,
            uplink_host=args.uplink_host,
            uplink_port=args.uplink_port,
            simulate_eclipse=args.simulate_eclipse,
        )
        print("\n" + "=" * 60)
        print("REFINED CDM PACKET GENERATED BY GROUND AI NODE")
        print("=" * 60)
        print(json.dumps(pkt, indent=2))
        print("=" * 60 + "\n")
        print("[*] Keeping process alive for 15s to allow UI WebSockets to flush...")
        time.sleep(15)


if __name__ == "__main__":
    main()
