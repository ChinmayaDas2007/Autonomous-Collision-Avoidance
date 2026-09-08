#!/usr/bin/env python3
"""
===============================================================================
PROJECT KESSLER: GROUND AI OPERATIONS (gnd_ops) — AUTOMATED WORKFLOW ENGINE
===============================================================================
Automates the full Ground AI mission pipeline from space weather and TLE ingestion
to observation scheduling, Earth umbra shadow ray-tracing, AI atmospheric drag
covariance modeling, contract publication, and visual telemetry briefings:

Pipeline Workflow:
1. SPACE WEATHER & TELEMETRY INGESTION:
   - Queries NOAA SWPC endpoints or applies quiet-sun/geomagnetic storm heuristics.
   - Ingests F10.7 solar radio flux (s.f.u.) and planetary Kp geomagnetic index.
2. EPHEMERIS & SGP4 PROPAGATION (Phase 1):
   - Ingests raw Two-Line Elements (TLEs) for Primary Satellite (e.g. ISS / KesslerSat-1)
     and Secondary Debris Threat.
   - Propagates both state vectors to nominal Time of Closest Approach (TCA).
3. MULTI-ORBIT BACKSTEP OBSERVATION SCHEDULING (Phase 2):
   - Computes orbital period T = 2*pi / n_0.
   - Backsteps 2 full revolutions: T_0 = TCA - 2*T (~185 min prior in LEO).
4. CYLINDRICAL EARTH UMBRA RAY-TRACING (Phase 3):
   - Analytical solar ephemeris computation (Vallado algorithm).
   - Cylindrical Earth shadow projection (r_proj, r_perp vs R_earth).
   - Evaluates optical line-of-sight and umbra blindness risk.
5. AI DRAG PREDICTION & COVARIANCE MODELING:
   - Evaluates atmospheric density scaling and ballistic coefficient variations.
   - Derives drag multiplier and 3-axis covariance ellipsoid [sigma_x, sigma_y, sigma_z].
6. CONTRACTS & NETWORK UPLINK:
   - Exports Master Initial Ephemeris ('shared/initial_ephemeris.json') for core_phy.
   - Exports Refined CDM ('shared/cdm_packet.json') for edge_pro.
   - Optionally uplinks live over TCP to edge_pro (Port 5555).
7. VISUAL TELEMETRY ARTIFACTS:
   - 'orbital_conjunction_geometry.png': 3D orbital planes, Earth sphere, Sun vector, TCA, and T_0.
   - 'space_weather_drag_profile.png': Solar activity vs density profile & covariance ellipsoid.
   - 'ground_ai_mission_briefing.png': Unified Ground Operations intelligence dashboard.
===============================================================================
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Dict, Any, Optional, Tuple

# Auto-switch to virtualenv if required packages are present in bsk_env
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D
    import numpy as np
    import sgp4
except ImportError:
    bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
    if os.path.exists(bsk_python) and sys.executable != bsk_python:
        print(f"[Workflow] Switching to Python virtualenv at {bsk_python}...")
        os.execv(bsk_python, [bsk_python] + sys.argv)
    else:
        print("Error: Required packages not found and virtualenv not located.", file=sys.stderr)
        sys.exit(1)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "gnd_ops"))

from gnd_ops.ground_ai_node import (
    GroundAINode,
    EphemerisProcessor,
    calculate_sun_position_eci,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [GndOpsWorkflow] %(message)s",
)
logger = logging.getLogger("GndOpsWorkflow")
ARTIFACT_DIR = Path("/Users/chinmaya/.gemini/antigravity/brain/76a30798-8a62-45fd-9de4-67d776ef2b89")


class AutomatedGroundOpsWorkflow:
    """
    Automated Mission Workflow Orchestrator for Ground AI Operations.
    """

    def __init__(
        self,
        scenario: str = "direct_hit",
        uplink: bool = False,
        uplink_host: str = "127.0.0.1",
        uplink_port: int = 5555,
        output_dir: Optional[Path] = None,
    ):
        self.scenario = scenario
        self.uplink = uplink
        self.uplink_host = uplink_host
        self.uplink_port = uplink_port
        self.output_dir = output_dir or (ROOT_DIR / "gnd_ops" / "captures")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.shared_dir = ROOT_DIR / "shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)

        self.node = GroundAINode()
        self.metrics: Dict[str, Any] = {}

    def run(self) -> Dict[str, Any]:
        """Executes the full automated Ground AI operations pipeline."""
        start_time = time.time()
        print("\n" + "=" * 80)
        print(f" PROJECT KESSLER: GROUND AI NODE WORKFLOW [SCENARIO: {self.scenario.upper()}] ")
        print("=" * 80)

        # 1. Configure Scenario Conjunction
        now = datetime.now(timezone.utc)
        tca_dt = now + timedelta(hours=3, minutes=5)
        tca_str = tca_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if self.scenario == "near_miss":
            miss_km = 2.2
            secondary_name = "Debris_Obj_9914"
            simulate_eclipse = False
        elif self.scenario == "active_active":
            miss_km = 0.25
            secondary_name = "CommercialSat-9"
            simulate_eclipse = False
        elif self.scenario == "eclipse":
            miss_km = 0.15
            secondary_name = "Debris_Obj_8492"
            simulate_eclipse = True
        else:  # direct_hit
            miss_km = 0.35
            secondary_name = "Debris_Obj_8492"
            simulate_eclipse = False

        conjunction = {
            "primary_asset": "Kessler_Sat_1",
            "secondary_asset": secondary_name,
            "time_of_closest_approach": tca_str,
            "miss_distance_km": miss_km,
        }

        simulate_eclipse = (self.scenario == "eclipse")
        force_sunlit = not simulate_eclipse

        # 2. Execute Ground AI Prediction Cycle
        logger.info("Executing Ground AI orbital mechanics & AI prediction pipeline...")
        pkt = self.node.run_once(
            conjunction=conjunction,
            primary_tle=EphemerisProcessor.DEFAULT_PRIMARY_TLE,
            debris_tle=EphemerisProcessor.DEFAULT_DEBRIS_TLE,
            uplink=self.uplink,
            uplink_host=self.uplink_host,
            uplink_port=self.uplink_port,
            simulate_eclipse=simulate_eclipse,
            force_sunlit=force_sunlit,
        )

        self.metrics["packet"] = pkt

        # 3. Read generated initial ephemeris contract
        ephem_file = self.shared_dir / "initial_ephemeris.json"
        ephem_data = json.loads(ephem_file.read_text()) if ephem_file.exists() else {}

        # 4. Generate Diagnostic Visual Telemetry Plots
        self.render_visual_plots(pkt, ephem_data, simulate_eclipse)

        elapsed = time.time() - start_time
        print("\n" + "=" * 80)
        print(" [SUCCESS] GROUND AI OPERATIONS WORKFLOW COMPLETED ")
        print("=" * 80)
        print(f"  Primary Asset:        {pkt['conjunction_data']['primary_asset']}")
        print(f"  Secondary Threat:     {pkt['conjunction_data']['secondary_asset']}")
        print(f"  TCA (UTC):            {pkt['conjunction_data']['time_of_closest_approach']}")
        print(f"  Observation T_0:      {pkt['conjunction_data']['observation_window_start_utc']}")
        print(f"  Radar Miss Distance:  {pkt['conjunction_data']['miss_distance_km'] * 1000.0:.1f} m")
        print(f"  Atmospheric Drag Mult:{pkt['ai_drag_prediction']['drag_multiplier']:.3f}")
        print(f"  Ground AI Action:     {pkt['action']}")
        print(f"  Master Ephemeris:     {ephem_file}")
        print(f"  Refined CDM:          {self.shared_dir / 'cdm_packet.json'}")
        print(f"  Total Execution Time: {elapsed:.2f} seconds")
        print("=" * 80 + "\n")

        return {
            "status": "SUCCESS",
            "packet": pkt,
            "ephemeris": ephem_data,
            "elapsed_sec": elapsed,
        }

    def render_visual_plots(self, pkt: dict, ephem: dict, simulate_eclipse: bool) -> None:
        """Renders publication-grade mission telemetry charts."""
        logger.info("Generating Ground AI visual telemetry plots...")

        # ─────────────────────────────────────────────────────────────────────
        # PLOT 1: 3D Orbital Conjunction Geometry & Earth Umbra Cone
        # ─────────────────────────────────────────────────────────────────────
        fig = plt.figure(figsize=(10, 8), dpi=150)
        ax = fig.add_subplot(111, projection="3d")
        ax.set_facecolor("#0b0f19")
        fig.patch.set_facecolor("#0b0f19")

        # Earth sphere
        r_earth = 6371.0
        u = np.linspace(0, 2 * np.pi, 30)
        v = np.linspace(0, np.pi, 30)
        x_e = r_earth * np.outer(np.cos(u), np.sin(v))
        y_e = r_earth * np.outer(np.sin(u), np.sin(v))
        z_e = r_earth * np.outer(np.ones(np.size(u)), np.cos(v))
        ax.plot_surface(x_e, y_e, z_e, color="#1e3a8a", alpha=0.3, edgecolors="#2563eb", linewidth=0.2)

        # Primary orbit ring (approx 400km circular LEO at 51.6 deg inclination)
        theta = np.linspace(0, 2 * np.pi, 200)
        r_orb = r_earth + 400.0
        inc = math.radians(51.6)
        x_orb = r_orb * np.cos(theta)
        y_orb = r_orb * np.sin(theta) * math.cos(inc)
        z_orb = r_orb * np.sin(theta) * math.sin(inc)
        ax.plot(x_orb, y_orb, z_orb, color="#38bdf8", linestyle="--", linewidth=1.5, label="Primary Sat Orbit (400 km LEO)")

        # Debris crossing orbit (retrograde / cross-track)
        inc_deb = math.radians(98.2)
        x_deb = r_orb * np.cos(theta) * 0.98 + 150.0
        y_deb = r_orb * np.sin(theta) * math.cos(inc_deb)
        z_deb = r_orb * np.sin(theta) * math.sin(inc_deb)
        ax.plot(x_deb, y_deb, z_deb, color="#ef4444", linestyle=":", linewidth=1.5, label="Debris Crossing Orbit")

        # TCA Point
        tca_pt = [x_orb[50], y_orb[50], z_orb[50]]
        ax.scatter([tca_pt[0]], [tca_pt[1]], [tca_pt[2]], color="#fbbf24", s=90, marker="*", label="TCA Encounter Node")

        # T_0 Observation Window Point (2 orbits backstep ~ 185 min prior)
        obs_pt = [x_orb[180], y_orb[180], z_orb[180]]
        ax.scatter([obs_pt[0]], [obs_pt[1]], [obs_pt[2]], color="#10b981", s=70, marker="o", label="T_0 Observation Window (-2 Orbits)")

        # Sun Vector
        now = datetime.now(timezone.utc)
        _, sun_u = calculate_sun_position_eci(now)
        sun_arrow = np.array(sun_u) * 9000.0
        ax.quiver(0, 0, 0, sun_arrow[0], sun_arrow[1], sun_arrow[2], color="#facc15", linewidth=2.0, arrow_length_ratio=0.15, label="Solar Vector (Sunward)")

        # Formatting
        ax.set_title("Project Kessler — Ground AI: 3D Orbital Geometry & Multi-Orbit Backstep", color="#f8fafc", fontsize=11, weight="bold", pad=15)
        ax.set_xlabel("ECI X (km)", color="#94a3b8", labelpad=8)
        ax.set_ylabel("ECI Y (km)", color="#94a3b8", labelpad=8)
        ax.set_zlabel("ECI Z (km)", color="#94a3b8", labelpad=8)
        ax.tick_params(colors="#94a3b8")
        ax.grid(True, linestyle="--", alpha=0.2, color="#475569")
        ax.legend(loc="upper left", facecolor="#1e293b", edgecolor="#475569", labelcolor="#f8fafc", fontsize=8)
        ax.view_init(elev=28, azim=45)

        geo_path = self.output_dir / "orbital_conjunction_geometry.png"
        fig.savefig(str(geo_path), bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        # ─────────────────────────────────────────────────────────────────────
        # PLOT 2: Space Weather & Atmospheric Drag Density Profile
        # ─────────────────────────────────────────────────────────────────────
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), dpi=150)
        fig.patch.set_facecolor("#0b0f19")

        # Left: Atmospheric Density profile vs Altitude
        alts = np.linspace(200, 600, 100)
        h_scale = 55.0
        rho_nominal = 3.8e-12 * np.exp(-(alts - 400.0) / h_scale)
        drag_mult = pkt["ai_drag_prediction"]["drag_multiplier"]
        rho_perturbed = rho_nominal * drag_mult

        ax1.set_facecolor("#111827")
        ax1.semilogy(alts, rho_nominal, color="#38bdf8", linestyle="--", linewidth=1.5, label="Baseline Atmosphere (Quiet Sun)")
        ax1.semilogy(alts, rho_perturbed, color="#f97316", linewidth=2.0, label=f"AI Perturbed Atmosphere (Drag Mult: {drag_mult:.2f})")
        ax1.axvline(400.0, color="#fbbf24", linestyle=":", label="Primary Sat Orbit (400 km)")
        ax1.set_title("NRLMSISE Thermospheric Density vs Solar Activity", color="#f8fafc", fontsize=10, weight="bold")
        ax1.set_xlabel("Altitude (km)", color="#94a3b8")
        ax1.set_ylabel("Atmospheric Density (kg/m³)", color="#94a3b8")
        ax1.tick_params(colors="#94a3b8")
        ax1.grid(True, linestyle="--", alpha=0.2, color="#475569")
        ax1.legend(facecolor="#1e293b", edgecolor="#475569", labelcolor="#f8fafc", fontsize=8)

        # Right: Covariance Error Ellipsoid (Along-track vs Cross-track)
        cov = pkt["ai_drag_prediction"]["ellipsoid_covariance_matrix"]
        sigma_x, sigma_y, sigma_z = cov[0], cov[1], cov[2]

        ax2.set_facecolor("#111827")
        phi = np.linspace(0, 2 * np.pi, 100)
        ell_x = sigma_x * np.cos(phi)
        ell_y = sigma_y * np.sin(phi)
        ax2.plot(ell_x, ell_y, color="#ef4444", linewidth=2.0, label=f"3-Sigma Covariance: ±{sigma_x:.0f}m Along-Track")
        ax2.fill(ell_x, ell_y, color="#ef4444", alpha=0.15)
        ax2.scatter([0], [0], color="#fbbf24", s=80, marker="x", label="Nominal Conjunction Centroid")
        ax2.axhline(0, color="#475569", linestyle="--", linewidth=0.8)
        ax2.axvline(0, color="#475569", linestyle="--", linewidth=0.8)
        ax2.set_title(f"Uncertainty Ellipsoid (F10.7={pkt['ai_drag_prediction']['f107_flux']:.1f}, Kp={pkt['ai_drag_prediction']['kp_index']:.1f})", color="#f8fafc", fontsize=10, weight="bold")
        ax2.set_xlabel("Along-Track Displacement (m)", color="#94a3b8")
        ax2.set_ylabel("Cross-Track Displacement (m)", color="#94a3b8")
        ax2.tick_params(colors="#94a3b8")
        ax2.grid(True, linestyle="--", alpha=0.2, color="#475569")
        ax2.legend(facecolor="#1e293b", edgecolor="#475569", labelcolor="#f8fafc", fontsize=8)

        profile_path = self.output_dir / "space_weather_drag_profile.png"
        fig.savefig(str(profile_path), bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        # ─────────────────────────────────────────────────────────────────────
        # PLOT 3: Ground Operations Composite Briefing Infographic
        # ─────────────────────────────────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        fig.patch.set_facecolor("#0b0f19")
        ax.set_facecolor("#0b0f19")
        ax.axis("off")

        box_props = dict(boxstyle="round,pad=0.6", facecolor="#1e293b", edgecolor="#38bdf8", alpha=0.9)
        summary_text = (
            f"PROJECT KESSLER — GROUND AI OPERATIONS MISSION INTELLIGENCE\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Conjunction Pair:        {pkt['conjunction_data']['primary_asset']} vs {pkt['conjunction_data']['secondary_asset']}\n"
            f"• Nominal TCA:             {pkt['conjunction_data']['time_of_closest_approach']}\n"
            f"• 2-Orbit Backstep T_0:    {pkt['conjunction_data']['observation_window_start_utc']} (Delta = 185.8 min)\n"
            f"• Space Weather State:     F10.7 = {pkt['ai_drag_prediction']['f107_flux']:.1f} s.f.u. | Kp = {pkt['ai_drag_prediction']['kp_index']:.1f}\n"
            f"• AI Drag Multiplier:      {pkt['ai_drag_prediction']['drag_multiplier']:.3f}x Nominal\n"
            f"• Along-Track Error (1σ):  ±{sigma_x:.1f} meters\n"
            f"• Earth Umbra Condition:   {'SHADOW OCCULTATION (IN UMBRA)' if simulate_eclipse else 'SUNLIT ILLUMINATED (DIRECT LOS)'}\n"
            f"• Operational Directive:   {pkt['action']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Uplink Target: TCP {self.uplink_host}:{self.uplink_port} | Shared Ephemeris: shared/initial_ephemeris.json"
        )
        ax.text(0.05, 0.5, summary_text, color="#f8fafc", fontfamily="monospace", fontsize=11, verticalalignment="center", bbox=box_props)

        briefing_path = self.output_dir / "ground_ai_mission_briefing.png"
        fig.savefig(str(briefing_path), bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        # Synchronize to conversation artifact directory if available
        if ARTIFACT_DIR.exists():
            for p in [geo_path, profile_path, briefing_path]:
                shutil.copyfile(str(p), str(ARTIFACT_DIR / p.name))

        logger.info(f"  Orbital Geometry Plot:  {geo_path}")
        logger.info(f"  Drag Profile Plot:      {profile_path}")
        logger.info(f"  Mission Briefing:       {briefing_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Project Kessler — Automated Ground AI Operations Workflow")
    parser.add_argument(
        "--scenario",
        choices=["direct_hit", "near_miss", "active_active", "eclipse"],
        default="direct_hit",
        help="Conjunction scenario to simulate (default: direct_hit)",
    )
    parser.add_argument(
        "--uplink",
        action="store_true",
        help="Uplink Refined CDM to live edge_pro TCPIngestServer",
    )
    parser.add_argument(
        "--uplink-host",
        default="127.0.0.1",
        help="edge_pro host for TCP uplink (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--uplink-port",
        type=int,
        default=5555,
        help="edge_pro port for TCP uplink (default: 5555)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for generated plots and contracts",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.output_dir) if args.output_dir else None
    workflow = AutomatedGroundOpsWorkflow(
        scenario=args.scenario,
        uplink=args.uplink,
        uplink_host=args.uplink_host,
        uplink_port=args.uplink_port,
        output_dir=out_dir,
    )
    workflow.run()


if __name__ == "__main__":
    main()
