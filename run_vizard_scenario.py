#!/usr/bin/env python3
"""
===============================================================================
PROJECT KESSLER: BASILISK VIZARD 3D SCENARIO GENERATOR
===============================================================================
Generates a full 3D orbital, attitude slew maneuver, and debris flyby simulation
playable directly in Vizard.app.

Operational Sequence:
1. Spawns Primary_Sat (750 kg, 400 km LEO orbit, 4 reaction wheels, Star Tracker).
2. Spawns Debris_Obj_8492 on a cross-track collision trajectory (TCA = 120s, miss = 100m).
3. Phase 1 (0 to 20s): Spacecraft cruises in nominal nadir/inertial orientation.
4. Phase 2 (20s to 65s): Slew Command dispatched! MRP controller commands reaction
   wheels to slew the spacecraft, locking the Star Tracker boresight onto the threat vector.
5. Phase 3 (65s to 150s): Optical sensor locked on threat. At TCA (120s), the debris
   streaks directly through the Star Tracker camera's field of view!
6. Exports 'kessler_slew_scenario.bin' for 3D playback in Vizard.app.

Usage:
  ./edge_pro/run_vizard_scenario.py
  python3 edge_pro/run_vizard_scenario.py --open
===============================================================================
"""

import os
import sys
import subprocess

# Auto-detect and switch to bsk_env virtualenv if Basilisk is not in current environment
try:
    import Basilisk
except ImportError:
    bsk_python = "/Users/chinmaya/bsk_env/bin/python3"
    if os.path.exists(bsk_python) and sys.executable != bsk_python:
        print(f"[Launcher] Switching to Basilisk virtualenv at {bsk_python}...")
        os.execv(bsk_python, [bsk_python] + sys.argv)
    else:
        print("Error: Basilisk module not found and bsk_env not located.", file=sys.stderr)
        sys.exit(1)

import argparse
import math
import numpy as np

# Add core_phy to path to import KesslerTestbench
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
possible_core_dirs = [
    os.path.join(SCRIPT_DIR, "core_phy"),
    os.path.join(SCRIPT_DIR, "..", "core_phy"),
    "/Users/chinmaya/Desktop/Project_Kessler/core_phy",
]
for d in possible_core_dirs:
    d = os.path.abspath(d)
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)

from two_body_viz import KesslerTestbench
from Basilisk.architecture import messaging
from Basilisk.utilities import macros, vizSupport


def vector_to_mrp(v_source: np.ndarray, v_target: np.ndarray) -> list[float]:
    """Computes the Modified Rodrigues Parameter (MRP) rotating v_source to v_target."""
    v1 = v_source / np.linalg.norm(v_source)
    v2 = v_target / np.linalg.norm(v_target)
    cross = np.cross(v1, v2)
    c_norm = np.linalg.norm(cross)
    if c_norm < 1e-8:
        if np.dot(v1, v2) > 0:
            return [0.0, 0.0, 0.0]
        else:
            return [1.0, 0.0, 0.0]
    axis = cross / c_norm
    phi = np.arctan2(c_norm, np.dot(v1, v2))
    mrp = np.tan(phi / 4.0) * axis
    return [float(mrp[0]), float(mrp[1]), float(mrp[2])]


def generate_vizard_scenario(output_filename: str = "kessler_slew_scenario.bin", auto_open: bool = False):
    output_path = os.path.abspath(output_filename)

    print("\n" + "=" * 76)
    print(" PROJECT KESSLER: BASILISK FLIGHT DYNAMICS & VIZARD SIMULATOR ")
    print("=" * 76)
    print("Initializing 6-DOF physics engine and FSW attitude control loops...")

    # 1. Initialize Testbench (10 Hz Dynamics, 2 Hz Flight Software)
    bench = KesslerTestbench(dyn_step_hz=10.0, fsw_step_hz=2.0)
    bench.configure_environment(include_j2=False)

    # 2. Primary Spacecraft Setup (400 km circular LEO orbit)
    r_mag = (6371.0 + 400.0) * 1000.0  # meters
    v_mag = math.sqrt(bench.earth.mu / r_mag)  # ~7672.6 m/s

    r_sat0 = [r_mag, 0.0, 0.0]
    v_sat0 = [0.0, v_mag, 0.0]

    print(f"Spawning Primary_Sat (750 kg) at 400 km LEO (Speed: {v_mag:.1f} m/s)...")
    sc_sat = bench.spawn_spacecraft("Primary_Sat", 750.0, rv_vectors=(r_sat0, v_sat0))
    sc_sat.hub.sigma_BNInit = [0.0, 0.0, 0.0]
    sc_sat.hub.omega_BN_BInit = [0.0, 0.0, 0.0]

    # Install Reaction Wheels and MRP Controller
    bench.configure_adcs("Primary_Sat")

    # 3. Conjunction Setup: Debris approaching with TCA at t = 120s (2.0 min)
    t_tca = 120.0  # seconds
    omega = v_mag / r_mag
    theta_tca = omega * t_tca

    # Satellite state at TCA
    r_sat_tca = [r_mag * math.cos(theta_tca), r_mag * math.sin(theta_tca), 0.0]
    v_sat_tca = [-v_mag * math.sin(theta_tca), v_mag * math.cos(theta_tca), 0.0]

    # Debris state at TCA (cross-track intercept, 100m miss distance for dramatic Vizard camera view)
    r_deb_tca = [r_sat_tca[0], r_sat_tca[1], 100.0]  # 100m above orbital plane
    v_deb_tca = [
        -v_mag * math.sin(theta_tca) * 0.75,
        v_mag * math.cos(theta_tca) * 0.75,
        4500.0  # 4.5 km/s cross-track velocity
    ]

    print(f"Back-calculating Debris initial state for TCA at T+120s (Miss distance: 100m)...")
    r_deb0, v_deb0 = bench.back_calculate_initial_state((r_deb_tca, v_deb_tca), duration_min=2.0)

    print("Spawning Debris_Obj_8492 (High-velocity threat asset)...")
    sc_deb = bench.spawn_spacecraft("Debris_Obj_8492", 50.0, rv_vectors=(r_deb0, v_deb0))

    # 4. Enable Vizard 3D Visualization Pipeline
    print(f"Configuring Vizard binary stream -> {output_path}...")
    viz = bench.enable_vizard(output_path)

    # Configure Orbit Colors in Vizard: Cyan for Primary_Sat, Bright Red for Debris
    try:
        viz.settings.spacecraftOrbitColors = [
            vizSupport.toRGBA255("cyan"),
            vizSupport.toRGBA255("red"),
        ]
    except Exception:
        pass

    # 5. Mount Star Tracker Camera on Primary_Sat (Looking along +X Body axis)
    camData = messaging.CameraConfigMsgPayload()
    camData.cameraID = 1
    camData.isOn = 1
    camData.fieldOfView = 25.0 * macros.D2R      # 25° Field of View
    camData.resolution = [1024, 1024]            # 1024x1024 high-res sensor
    camData.renderRate = macros.sec2nano(0.1)    # 10 Hz render rate
    camData.parentName = "Primary_Sat"
    camData.cameraPos_B = [1.5, 0.0, 0.0]        # Mounted on forward payload deck (+X)
    camData.sigma_CB = [0.0, 0.0, 0.0]           # Boresight pointing along +X body axis

    camMsg = messaging.CameraConfigMsg().write(camData)
    viz.addCamMsgToModule(camMsg)
    print("Star Tracker Optical Camera 1 mounted on Primary_Sat (+X Boresight, 25° FOV).")

    # 6. EXECUTE PHASE 1: Cruising in Nominal Orientation (0 to 20s)
    print("\n--- PHASE 1: Cruising in Nominal Standby (0 to 20 seconds) ---")
    bench.sim.InitializeSimulation()
    bench.sim.ConfigureStopTime(macros.sec2nano(20.0))
    bench.sim.ExecuteSimulation()
    print("Nominal standby completed. Reaction wheels steady.")

    # 7. EXECUTE PHASE 2: Slew Command from Edge Payload at T+20s
    print("\n--- PHASE 2: Slew Command Issued from edge_pro (T+20s) ---")
    r_sat_20 = bench.recorders["Primary_Sat"].r_BN_N[-1]
    r_deb_20 = bench.recorders["Debris_Obj_8492"].r_BN_N[-1]

    # Calculate line-of-sight vector from satellite to debris at t=20s
    los_vector = r_deb_20 - r_sat_20
    range_km = np.linalg.norm(los_vector) / 1000.0
    los_unit = los_vector / np.linalg.norm(los_vector)

    # Compute target MRP to point +X camera boresight along LOS vector
    slew_mrp = vector_to_mrp(np.array([1.0, 0.0, 0.0]), los_unit)
    slew_angle_deg = math.degrees(4.0 * math.atan(np.linalg.norm(slew_mrp)))

    print(f"Debris Range at Slew Initiation: {range_km:.1f} km")
    print(f"Commanded Slew Angle:            {slew_angle_deg:.2f}°")
    print(f"Commanded Target MRP (sigma_R0N):[{slew_mrp[0]:.4f}, {slew_mrp[1]:.4f}, {slew_mrp[2]:.4f}]")

    # Update attitude reference in FSW
    bench.attRef.sigma_R0N = slew_mrp

    # Continue simulation through slew convergence and debris encounter (up to 150s)
    print("\n--- PHASE 3: ADCS Slew Execution & Optical Intercept (20s to 150s) ---")
    print("Reaction wheels accelerating... Spacecraft re-orienting towards debris vector...")
    bench.sim.ConfigureStopTime(macros.sec2nano(150.0))
    bench.sim.ExecuteSimulation()

    # Final telemetry extraction
    r_sat_final = bench.recorders["Primary_Sat"].r_BN_N
    r_deb_final = bench.recorders["Debris_Obj_8492"].r_BN_N
    deltas = [np.linalg.norm(r_deb_final[i] - r_sat_final[i]) for i in range(len(r_sat_final))]
    min_dist_m = min(deltas)
    min_dist_idx = deltas.index(min_dist_m)
    tca_actual_sec = min_dist_idx * 0.1

    print("\n" + "=" * 76)
    print(" SIMULATION COMPLETE & VIZARD BINARY GENERATED ")
    print("=" * 76)
    print(f"Vizard Binary File:       {output_path}")
    print(f"File Size:                {os.path.getsize(output_path) / (1024 * 1024):.2f} MB")
    print(f"Actual TCA Occurred At:   T+{tca_actual_sec:.1f}s")
    print(f"Closest Approach Distance:{min_dist_m:.2f} meters")
    print("-" * 76)
    print("INSTRUCTIONS TO VIEW IN VIZARD:")
    print("  1. Open Vizard.app (located at /Users/chinmaya/Desktop/Vizard.app).")
    print(f"  2. Click 'Open File' and select:")
    print(f"     {output_path}")
    print("  3. Press PLAY (spacebar or play icon) to watch the orbit.")
    print("  4. At T+20s, watch the satellite reaction wheels spin and the vehicle slew!")
    print("  5. In Vizard's top menu, click 'Camera' -> Switch to 'Camera 1 (Primary_Sat)'.")
    print("     You will see through the Star Tracker as the debris streaks across the view!")
    print("=" * 76 + "\n")

    if auto_open:
        vizard_app = "/Users/chinmaya/Desktop/Vizard.app"
        if os.path.exists(vizard_app):
            print(f"Launching {vizard_app}...")
            subprocess.run(["open", "-a", vizard_app, output_path])


def main():
    parser = argparse.ArgumentParser(description="Project Kessler: Basilisk Vizard Scenario Generator")
    parser.add_argument(
        "--output",
        default="kessler_slew_scenario.bin",
        help="Target .bin filename for Vizard playback.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Automatically launch Vizard.app after generating the scenario.",
    )
    args = parser.parse_args()

    generate_vizard_scenario(output_filename=args.output, auto_open=args.open)


if __name__ == "__main__":
    main()
