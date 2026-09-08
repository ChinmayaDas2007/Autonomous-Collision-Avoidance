"""
PROJECT KESSLER: ADCS SOFTWARE-IN-THE-LOOP (SIL) FOUNDATION
-------------------------------------------------------------------------------
Description: 
This script establishes the baseline Attitude Determination and Control System 
(ADCS) pipeline for a 750 kg satellite in Low Earth Orbit (LEO). It initializes 
a tumbling rigid body, propagates its physics at 10 Hz, and executes a closed-loop 
Modified Rodrigues Parameter (MRP) feedback controller to force the satellite's 
sensor boresight to align with the inertial origin [0, 0, 0].

Currently utilizes an ideal actuator (ExtForceTorque) to validate FSW math 
before introducing physical Reaction Wheel (RW) saturation and jitter limits.
-------------------------------------------------------------------------------
"""

import numpy as np
import matplotlib.pyplot as plt
from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody
from Basilisk.simulation import spacecraft, extForceTorque, simpleNav
from Basilisk.fswAlgorithms import inertial3D, attTrackingError, mrpFeedback
from Basilisk.architecture import messaging

def execute_kessler_sil():
    # =========================================================================
    # PHASE 1: TRUTH MODEL (PHYSICS ENGINE)
    # =========================================================================
    
    # 1. Master Clock & Process Allocation
    # Basilisk is time-driven. We create a master simulation class and a single 
    # process. The task runs at 10 Hz (0.1 seconds per tick). Every module attached 
    # to this task will evaluate its math once per tick in exact sequence.
    sim = SimulationBaseClass.SimBaseClass()
    simTaskName = "simTask"
    dynProcess = sim.CreateNewProcess("simProcess")
    dynProcess.addTask(sim.CreateNewTask(simTaskName, macros.sec2nano(0.1)))

    # 2. Environmental Gravity Model
    # Instantiate Earth as the central gravitational body to dictate orbital mechanics.
    gravFactory = simIncludeGravBody.gravBodyFactory()
    earth = gravFactory.createEarth()
    earth.isCentralBody = True
    
    # 3. Spacecraft Rigid Body Dynamics
    # This module calculates the 6-DOF physical reality (translation and rotation).
    scObject = spacecraft.Spacecraft()
    scObject.ModelTag = "kessler_sat"
    
    # Define physical mass properties. Controller stability depends entirely on 
    # this inertia tensor mapping. Maximum inertia axis is 900 kg-m^2.
    scObject.hub.mHub = 750.0 
    I_flat = [900., 0., 0., 0., 800., 0., 0., 0., 600.]
    scObject.hub.IHubPntBc_B = [[900., 0., 0.], [0., 800., 0.], [0., 0., 600.]]
    
    gravFactory.addBodiesTo(scObject)
    sim.AddModelToTask(simTaskName, scObject)

    # 4. Initial Orbital State (400km LEO)
    # Define exact geometry using Keplerian elements, then mathematically convert 
    # them to the Cartesian inertial position (rN) and velocity (vN) vectors 
    # required by the C++ integration engine.
    oe = orbitalMotion.ClassicElements()
    oe.a = (6371 + 400) * 1000 
    oe.e = 0.0001
    oe.i = 51.6 * macros.D2R
    oe.Omega = 0.0
    oe.omega = 0.0
    oe.f = 0.0
    
    rN, vN = orbitalMotion.elem2rv(earth.mu, oe)
    scObject.hub.r_CN_NInit = rN  
    scObject.hub.v_CN_NInit = vN  
    
    # Inject an initial attitude offset (MRP) and tumbling rate (rad/s).
    # This creates the physical error the controller must solve.
    scObject.hub.sigma_BNInit = [0.1, 0.2, -0.3]
    scObject.hub.omega_BN_BInit = [0.001, -0.01, 0.03]

    # =========================================================================
    # PHASE 2: FLIGHT SOFTWARE (FSW) PIPELINE
    # =========================================================================

    # 5. Sensor Suite (Navigation)
    # FSW cannot query the universe. simpleNav reads the spacecraft's true state 
    # and packages it into a navigation estimate. (Noise models are disabled here).
    sNav = simpleNav.SimpleNav()
    sNav.ModelTag = "nav_module"
    sim.AddModelToTask(simTaskName, sNav)

    # 6. Guidance Law
    # Dictates the target orientation. We command a static alignment with the 
    # inertial origin [0, 0, 0].
    attRef = inertial3D.inertial3D()
    attRef.ModelTag = "guidance_ref"
    attRef.sigma_R0N = [0., 0., 0.] 
    sim.AddModelToTask(simTaskName, attRef)

    # 7. Attitude Tracking Error (Comparator)
    # Calculates the exact delta between where the sensors say we are (sNav) 
    # and where we want to be (attRef).
    attError = attTrackingError.attTrackingError()
    attError.ModelTag = "tracking_error"
    sim.AddModelToTask(simTaskName, attError)

    # 8. MRP Feedback Controller
    # Proportional-Derivative (PD) control law. Computes the required physical 
    # torque (Nm) to drive the tracking error to zero without overshooting.
    # K (15.0) sets slew speed. P (150.0) provides damping based on the inertia tensor.
    mrpControl = mrpFeedback.mrpFeedback()
    mrpControl.ModelTag = "mrp_controller"
    mrpControl.K = 15.0 
    mrpControl.P = 150.0 
    mrpControl.Ki = -1.0 
    mrpControl.integralLimit = 0.0
    sim.AddModelToTask(simTaskName, mrpControl)

    # The FSW must know the vehicle's mass distribution to calculate torque accurately.
    configData = messaging.VehicleConfigMsgPayload()
    configData.ISCPntB_B = I_flat 
    configMsg = messaging.VehicleConfigMsg().write(configData)

    # 9. Ideal Actuator
    # Flawlessly applies the controller's requested torque to the physical rigid body.
    # (To be replaced by rwMotorTorque and reactionWheelStateEffector models later).
    extFT = extForceTorque.ExtForceTorque()
    extFT.ModelTag = "ideal_actuator"
    scObject.addDynamicEffector(extFT)
    sim.AddModelToTask(simTaskName, extFT)

    # =========================================================================
    # PHASE 3: ARCHITECTURE ROUTING & EXECUTION
    # =========================================================================

    # 10. Message Passing
    # BSK modules are strictly isolated. We physically wire the output payloads 
    # of one module to the input ports of the next to form the closed loop.
    sNav.scStateInMsg.subscribeTo(scObject.scStateOutMsg)
    attError.attNavInMsg.subscribeTo(sNav.attOutMsg)
    attError.attRefInMsg.subscribeTo(attRef.attRefOutMsg)
    mrpControl.guidInMsg.subscribeTo(attError.attGuidOutMsg)
    mrpControl.vehConfigInMsg.subscribeTo(configMsg)
    extFT.cmdTorqueInMsg.subscribeTo(mrpControl.cmdTorqueOutMsg)

    # 11. Telemetry Logging
    # Subscribe a recorder to the tracking error output to capture data per tick.
    dataLog = attError.attGuidOutMsg.recorder()
    sim.AddModelToTask(simTaskName, dataLog)

    # 12. Execution
    # Compile the C++ setup, configure the clock to stop at 3 minutes, and run.
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.min2nano(3.)) 
    sim.ExecuteSimulation()

    # =========================================================================
    # PHASE 4: VERIFICATION & PLOTTING
    # =========================================================================

    # Extract time arrays and MRP error states from the recorder.
    timeData = dataLog.times() * macros.NANO2MIN
    sigmaData = dataLog.sigma_BR
    
    # Render the telemetry to confirm asymptotic convergence to zero.
    plt.figure(figsize=(8,5))
    plt.plot(timeData, sigmaData[:, 0], label=r'$\sigma_1$')
    plt.plot(timeData, sigmaData[:, 1], label=r'$\sigma_2$')
    plt.plot(timeData, sigmaData[:, 2], label=r'$\sigma_3$')
    plt.axhline(0, color='black', linewidth=0.5)
    plt.title('Project Kessler: Attitude Error Convergence')
    plt.xlabel('Time [min]')
    plt.ylabel('MRP Error')
    plt.legend()
    plt.grid(True)
    plt.show()

if __name__ == "__main__":
    execute_kessler_sil()