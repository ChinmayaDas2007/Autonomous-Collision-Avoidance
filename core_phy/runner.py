from two_body_viz import KesslerTestbench
from Basilisk.architecture import messaging
from Basilisk.utilities import macros
import math

# 1. Initialize the master clock and testbench
bench = KesslerTestbench(dyn_step_hz=10.0, fsw_step_hz=2.0)
bench.configure_environment(include_j2=False)

# 2. Static Earth-Staring Setup (Isolating the Sensor)
r_static = [(6371.0 + 400.0) * 1000.0, 0.0, 0.0]
v_static = [0.0, 7660.0, 0.0]

print("Spawning calibration asset...")
sc = bench.spawn_spacecraft("Primary_Sat", 750.0, rv_vectors=(r_static, v_static))

# Zero out the tumble. The FSW will easily hold perfect inertial alignment.
sc.hub.sigma_BNInit = [0.0, 0.0, 0.0]
sc.hub.omega_BN_BInit = [0.0, 0.0, 0.0]

# 3. Boot the Flight Software
bench.configure_adcs("Primary_Sat")

# 4. Enable Telemetry
viz = bench.enable_vizard("kessler_targeting.bin")

# 5. Sensor Calibration: Pointing directly at the planet
camData = messaging.CameraConfigMsgPayload()
camData.cameraID = 1
camData.isOn = 1
camData.fov = 90.0 * macros.D2R           # Wide angle to capture the horizon
camData.resolution = [512, 512]
camData.renderRate = macros.sec2nano(0.1)
camData.parentName = "Primary_Sat"
camData.cameraPos_B = [-2.0, 0.0, 0.0]     # Mount 2m out on the Earth-facing hull (-X)
camData.sigma_CB = [0.0, -0.41421356, 0.0] # -90 degree MRP pitch to point at Nadir (-X)

camMsg = messaging.CameraConfigMsg()
camMsg.write(camData)
viz.addCamMsgToModule(camMsg)

# Calculate exact orbital period for 1 full orbit (~92.4 minutes at 400km altitude)
r_mag = (6371.0 + 400.0) * 1000.0
orbit_period_sec = 2 * math.pi * math.sqrt((r_mag**3) / bench.earth.mu)
orbit_period_min = orbit_period_sec / 60.0

print(f"Executing visual sensor calibration for 1 full orbit ({orbit_period_min:.2f} minutes)...")
bench.execute(sim_time_minutes=orbit_period_min)
print("Calibration complete. Load 'kessler_targeting.bin' into Vizard.")