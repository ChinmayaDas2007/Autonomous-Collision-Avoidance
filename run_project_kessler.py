#!/usr/bin/env python3
"""
===============================================================================
PROJECT KESSLER: AUTONOMOUS COLLISION AVOIDANCE SYSTEM — MASTER AUTOMATION RUNNER
===============================================================================
Unified command-line orchestrator executing the full end-to-end mission workflow
across all three core modules:

  1. Ground AI Node (gnd_ops)
     - SGP4 orbital propagation & 2-orbit backstep observation scheduling
     - Space weather ingestion (F10.7, Kp) & AI atmospheric drag prediction
     - Earth cylindrical umbra shadow ray-tracing & Master Ephemeris generation

  2. Flight Dynamics & ADCS (core_phy)
     - 6-DOF Basilisk rigid body dynamics (750 kg LEO satellite)
     - Closed-loop mrpFeedback attitude controller & 4-RW actuator array
     - 3D Vizard scenario generation & auto-launch in Vizard.app

  3. Edge Processing Payload (edge_pro)
     - ADCS settling jitter mitigation & Star Tracker frame streaming
     - Phase 2: OpenCV background star subtraction (|I_k - I_0| & |I_k - I_{k-1}|)
     - Phase 3: Liang-Barsky directed ray-AABB danger corridor projection
     - Phase 4: Active-Active multi-variable responsibility matrix & minimal Delta-V burn
     - 2D Software Reality HUD frame export & composite briefing generation

Usage:
  # Run entire end-to-end pipeline with direct collision scenario:
  python3 run_project_kessler.py --scenario direct_hit --open-vizard

  # Run near-miss scenario (verifies false-alarm corridor clearance):
  python3 run_project_kessler.py --scenario near_miss

  # Run active-active peer satellite negotiation scenario:
  python3 run_project_kessler.py --scenario active_active

  # Run Earth umbra shadow scenario (verifies optical abort to radar):
  python3 run_project_kessler.py --scenario eclipse

  # Run only a specific subsystem:
  python3 run_project_kessler.py --module edge_pro
  python3 run_project_kessler.py --module gnd_ops
  python3 run_project_kessler.py --module core_phy
===============================================================================
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Dict, Any, Optional

# Auto-switch to virtualenv if required packages are present in bsk_env
try:
    import cv2
    import numpy as np
    import matplotlib
except ImportError:
    bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
    if os.path.exists(bsk_python) and sys.executable != bsk_python:
        print(f"[Master Runner] Switching to Python virtualenv at {bsk_python}...")
        os.execv(bsk_python, [bsk_python] + sys.argv)
    else:
        print("Error: Required packages not found and virtualenv not located.", file=sys.stderr)
        sys.exit(1)

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from gnd_ops.workflow_automation import AutomatedGroundOpsWorkflow
from core_phy.workflow_automation import AutomatedCorePhyWorkflow
from edge_pro.workflow_automation import AutomatedEdgeWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MasterRunner] %(message)s",
)
logger = logging.getLogger("MasterRunner")

VIZARD_APP_PATH = "/Users/chinmaya/Desktop/Vizard.app"
ARTIFACT_DIR = Path("/Users/chinmaya/.gemini/antigravity/brain/76a30798-8a62-45fd-9de4-67d776ef2b89")


class MasterProjectKesslerRunner:
    """
    Top-Level Automated Orchestrator for Project Kessler.
    """

    def __init__(
        self,
        scenario: str = "direct_hit",
        module: str = "all",
        open_vizard: bool = False,
    ):
        self.scenario = scenario
        self.module = module
        self.open_vizard = open_vizard
        self.results: Dict[str, Any] = {}

    def run(self):
        start_time = time.time()
        self.print_banner()

        if self.module == "gnd_ops":
            self.run_gnd_ops()
        elif self.module == "core_phy":
            self.run_core_phy()
        elif self.module == "edge_pro":
            self.run_edge_pro()
        else:
            # Run Full End-to-End Mission Pipeline
            self.run_full_system()

        total_elapsed = time.time() - start_time
        self.print_summary(total_elapsed)

    def print_banner(self):
        print("\n" + "═" * 84)
        print("  PROJECT KESSLER: AUTONOMOUS COLLISION AVOIDANCE MISSION CONTROL")
        print("═" * 84)
        print(f"  Execution Mode:    {self.module.upper()}")
        print(f"  Mission Scenario:  {self.scenario.upper()}")
        print(f"  Auto-Open Vizard:  {self.open_vizard}")
        print(f"  Timestamp (UTC):   {datetime.now(timezone.utc).isoformat()}")
        print("═" * 84 + "\n")

    def run_gnd_ops(self) -> Dict[str, Any]:
        logger.info(">>> RUNNING GROUND AI OPERATIONS (gnd_ops) <<<")
        wf = AutomatedGroundOpsWorkflow(scenario=self.scenario)
        res = wf.run()
        self.results["gnd_ops"] = res
        return res

    def run_core_phy(self) -> Dict[str, Any]:
        logger.info(">>> RUNNING SPACECRAFT DYNAMICS & ADCS FSW (core_phy) <<<")
        wf = AutomatedCorePhyWorkflow(scenario=self.scenario, open_vizard=self.open_vizard)
        res = wf.run()
        self.results["core_phy"] = res
        return res

    def run_edge_pro(self) -> Dict[str, Any]:
        logger.info(">>> RUNNING EDGE PROCESSING PAYLOAD (edge_pro) <<<")
        wf = AutomatedEdgeWorkflow(scenario=self.scenario, open_vizard=self.open_vizard)
        res = wf.run()
        self.results["edge_pro"] = res
        return res

    def run_full_system(self):
        print("━" * 84)
        print("  STEP 1 / 3: GROUND AI NODE (ORBITAL MECHANICS & WEATHER PREDICTION)")
        print("━" * 84)
        gnd_res = self.run_gnd_ops()

        # If eclipse abort was triggered at Ground AI level, handle cleanly
        if gnd_res.get("packet", {}).get("action") == "ABORT_VISION_USE_GROUND_RADAR":
            logger.warning("[Ground AI Abort Directive] Satellite is in Earth umbra. Running edge verification of abort.")

        print("\n" + "━" * 84)
        print("  STEP 2 / 3: CORE_PHY PHYSICS (6-DOF BASILISK & 3D VIZARD GENERATION)")
        print("━" * 84)
        # Open Vizard in edge_pro step or here if requested
        core_res = self.run_core_phy()

        print("\n" + "━" * 84)
        print("  STEP 3 / 3: EDGE_PRO PAYLOAD (OPENCV DIFFERENCING & LIANG-BARSKY DECISION)")
        print("━" * 84)
        edge_res = self.run_edge_pro()

        self.results["pipeline"] = {
            "gnd_ops": gnd_res,
            "core_phy": core_res,
            "edge_pro": edge_res,
        }

    def print_summary(self, total_elapsed: float):
        print("\n" + "═" * 84)
        print("  PROJECT KESSLER: MISSION PIPELINE EXECUTION SUMMARY")
        print("═" * 84)

        if "gnd_ops" in self.results:
            pkt = self.results["gnd_ops"].get("packet", {})
            c_data = pkt.get("conjunction_data", {})
            drag = pkt.get("ai_drag_prediction", {})
            print(f"  [1] GROUND AI (gnd_ops):")
            print(f"      • Conjunction:        {c_data.get('primary_asset')} vs {c_data.get('secondary_asset')}")
            print(f"      • Backstep T_0 (UTC): {c_data.get('observation_window_start_utc')}")
            print(f"      • AI Drag Multiplier: {drag.get('drag_multiplier', 1.0):.3f} (F10.7={drag.get('f107_flux')}, Kp={drag.get('kp_index')})")
            print(f"      • Action Directive:   {pkt.get('action')}")

        if "core_phy" in self.results:
            print(f"  [2] FLIGHT DYNAMICS (core_phy):")
            print(f"      • 6-DOF Engine:       Basilisk C++ Core (10 Hz Dyn / 2 Hz FSW)")
            print(f"      • 3D Vizard Scenario: {self.results['core_phy'].get('scenario_bin')}")

        if "edge_pro" in self.results:
            edge = self.results["edge_pro"]
            dec = edge.get("decision", {})
            ass = edge.get("assessment", {})
            print(f"  [3] EDGE PAYLOAD (edge_pro):")
            print(f"      • Corridor Breach:    {ass.get('corridor_intersected')}")
            print(f"      • Decision Outcome:   {dec.get('decision')} ({dec.get('decision_status')})")
            print(f"      • Maneuver Duty:      {dec.get('responsibility')}")
            if dec.get("delta_v_magnitude_mps") is not None:
                print(f"      • Delta-V Avoidance:  {dec.get('delta_v_magnitude_mps'):.3f} m/s (Vector: {dec.get('delta_v_vector_mps')})")
                print(f"      • Burn Epoch:         {dec.get('burn_epoch_utc')}")
            print(f"      • 2D Software HUD:    shared/processed_frames/latest_corridor_hud.jpg")
            print(f"      • Mission Briefing:   edge_pro/captures/mission_pipeline_composite.png")

        print("─" * 84)
        print(f"  Total Pipeline Execution Time: {total_elapsed:.2f} seconds")
        print("═" * 84 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Project Kessler — Master Automation Runner")
    parser.add_argument(
        "--scenario",
        choices=["direct_hit", "near_miss", "active_active", "eclipse"],
        default="direct_hit",
        help="Conjunction mission scenario (default: direct_hit)",
    )
    parser.add_argument(
        "--module",
        choices=["all", "edge_pro", "gnd_ops", "core_phy"],
        default="all",
        help="Subsystem module to execute (default: all)",
    )
    parser.add_argument(
        "--open-vizard",
        action="store_true",
        help="Automatically open Vizard.app for 3D simulation playback",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    runner = MasterProjectKesslerRunner(
        scenario=args.scenario,
        module=args.module,
        open_vizard=args.open_vizard,
    )
    runner.run()


if __name__ == "__main__":
    main()
