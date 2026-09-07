import os
from Basilisk.simulation import spacecraft, reactionWheelStateEffector, simpleNav
from Basilisk.utilities import (
    SimulationBaseClass,
    macros,
    orbitalMotion,
    simIncludeGravBody,
    vizSupport,
    simIncludeRW
)
from Basilisk.fswAlgorithms import rwMotorTorque, mrpFeedback, attTrackingError, inertial3D
from Basilisk.architecture import messaging


class KesslerTestbench:

  def __init__(self, dyn_step_hz=10.0, fsw_step_hz=2.0):
    """Initializes simulation processes, clocks, and model containers."""
    self.sim = SimulationBaseClass.SimBaseClass()

    # Separate processes for Physics Dynamics and Flight Software
    self.dynProc = self.sim.CreateNewProcess("DynamicsProcess")
    self.fswProc = self.sim.CreateNewProcess("FSWProcess")

    self.dynTaskName = "dynTask"
    self.fswTaskName = "fswTask"

    self.dynProc.addTask(
        self.sim.CreateNewTask(self.dynTaskName, macros.sec2nano(1.0 / dyn_step_hz))
    )
    self.fswProc.addTask(
        self.sim.CreateNewTask(self.fswTaskName, macros.sec2nano(1.0 / fsw_step_hz))
    )

    self.sc_dict = {}
    self.recorders = {}
    self.gravFactory = simIncludeGravBody.gravBodyFactory()
    self.earth = None
    self.viz = None

  def configure_environment(self, include_j2=False):
    """Sets up the central body gravity field."""
    self.earth = self.gravFactory.createEarth()
    self.earth.isCentralBody = True

    if include_j2:
      self.earth.useSphericalHarmonicsGravityModel(
          simIncludeGravBody.gravBodyFactory.make_earth_spherical_harmonics_path(),
          2,
      )

  def spawn_spacecraft(
      self,
      sc_name,
      mass_kg,
      oe_elements=None,
      rv_vectors=None,
      inertia_diag=[10.0, 10.0, 10.0],
  ):
    """Spawns a 6-DOF rigid body using either Classical Orbital Elements or direct (r, v) vectors."""
    if self.earth is None:
      raise RuntimeError(
          "Environment must be configured before spawning assets."
      )

    sc = spacecraft.Spacecraft()
    sc.ModelTag = sc_name
    sc.hub.mHub = mass_kg
    sc.hub.IHubPntBc_B = [
        [inertia_diag[0], 0.0, 0.0],
        [0.0, inertia_diag[1], 0.0],
        [0.0, 0.0, inertia_diag[2]],
    ]

    # Resolve Cartesian initial conditions
    if rv_vectors is not None:
      rN, vN = rv_vectors
    elif oe_elements is not None:
      rN, vN = orbitalMotion.elem2rv(self.earth.mu, oe_elements)
    else:
      raise ValueError(
          "Must provide either Classical Orbital Elements or direct (r, v)"
          " vectors."
      )

    sc.hub.r_CN_NInit = rN
    sc.hub.v_CN_NInit = vN

    # Attach gravitational bodies and physics task
    self.gravFactory.addBodiesTo(sc)
    self.sim.AddModelToTask(self.dynTaskName, sc)

    # Attach state message recorder for trajectory extraction
    self.recorders[sc_name] = sc.scStateOutMsg.recorder()
    self.sim.AddModelToTask(self.dynTaskName, self.recorders[sc_name])

    self.sc_dict[sc_name] = sc
    return sc

  def back_calculate_initial_state(self, collision_rv, duration_min):
    """Runs a dedicated time-reversed simulation to determine initial states (t0)
    guaranteeing conjunction at TCA.
    """
    r_tca, v_tca = collision_rv

    # Spawn isolated temp sim
    temp_sim = SimulationBaseClass.SimBaseClass()
    proc = temp_sim.CreateNewProcess("BackPropProcess")
    proc.addTask(temp_sim.CreateNewTask("backTask", macros.sec2nano(0.5)))

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "backPropProbe"
    sc.hub.mHub = 100.0
    sc.hub.r_CN_NInit = r_tca
    sc.hub.v_CN_NInit = [-v for v in v_tca]  # Invert velocity vector

    grav_factory = simIncludeGravBody.gravBodyFactory()
    earth = grav_factory.createEarth()
    earth.isCentralBody = True
    grav_factory.addBodiesTo(sc)

    recorder = sc.scStateOutMsg.recorder()
    temp_sim.AddModelToTask("backTask", sc)
    temp_sim.AddModelToTask("backTask", recorder)

    temp_sim.InitializeSimulation()
    temp_sim.ConfigureStopTime(macros.min2nano(duration_min))
    temp_sim.ExecuteSimulation()

    # Extract final position and re-invert velocity for forward propagation
    r_initial = recorder.r_BN_N[-1]
    v_initial = [-v for v in recorder.v_BN_N[-1]]

    return r_initial, v_initial


  def execute(self, sim_time_minutes):
    """Initializes and runs the primary forward simulation."""
    self.sim.InitializeSimulation()
    self.sim.ConfigureStopTime(macros.min2nano(sim_time_minutes))
    self.sim.ExecuteSimulation()


  def enable_vizard(self, filename="kessler_sim.bin"):
    """Binds the telemetry stream to Vizard and retains the interface handle."""
    if not self.sc_dict:
      raise RuntimeError("No spacecraft spawned to visualize.")

    # Basilisk requires the effector list to exactly match the spacecraft list length.
    # We append None for any spacecraft (like the debris) that lacks reaction wheels.
    rw_effectors = []
    for sc_name in self.sc_dict.keys():
        if sc_name == "Primary_Sat" and hasattr(self, 'rwStateEffector'):
            rw_effectors.append(self.rwStateEffector)
        else:
            rw_effectors.append(None)

    self.viz = vizSupport.enableUnityVisualization(
        self.sim,
        self.dynTaskName,
        list(self.sc_dict.values()),
        saveFile=os.path.abspath(filename),
        rwEffectorList=rw_effectors
    )
    return self.viz


 
  def configure_adcs(self, sc_name):
    """Installs a physical 4-wheel RW pyramid and closed-loop FSW."""
    sc = self.sc_dict[sc_name]

    # 1. Physical Reaction Wheels (Truth)
    self.rwFactory = simIncludeRW.rwFactory()
    
    rw_params = {'maxMomentum': 50.0, 'maxTorque': 0.2, 'Omega': 0.0}
    self.rwFactory.create('Honeywell_HR16', [0.57735, 0.57735, 0.57735], **rw_params)
    self.rwFactory.create('Honeywell_HR16', [0.57735, -0.57735, 0.57735], **rw_params)
    self.rwFactory.create('Honeywell_HR16', [-0.57735, -0.57735, 0.57735], **rw_params)
    self.rwFactory.create('Honeywell_HR16', [-0.57735, 0.57735, 0.57735], **rw_params)
    
    self.rwStateEffector = reactionWheelStateEffector.ReactionWheelStateEffector()
    self.rwStateEffector.ModelTag = "RW_cluster"
    self.rwFactory.addToSpacecraft(self.rwStateEffector.ModelTag, self.rwStateEffector, sc)
    self.sim.AddModelToTask(self.dynTaskName, self.rwStateEffector)

    # 2. Flight Software: Sensor & Control Law
    self.sNav = simpleNav.SimpleNav()
    self.sNav.ModelTag = "nav_module"
    self.sim.AddModelToTask(self.fswTaskName, self.sNav)

    self.attRef = inertial3D.inertial3D()
    self.attRef.ModelTag = "guidance_ref"
    self.attRef.sigma_R0N = [0., 0., 0.]  
    self.sim.AddModelToTask(self.fswTaskName, self.attRef)

    self.attError = attTrackingError.attTrackingError()
    self.attError.ModelTag = "tracking_error"
    self.sim.AddModelToTask(self.fswTaskName, self.attError)

    self.mrpControl = mrpFeedback.mrpFeedback()
    self.mrpControl.ModelTag = "mrp_controller"
    self.mrpControl.K = 5.0   # Lowered to prevent aggressive overshoot
    self.mrpControl.P = 15.0  # Lowered to maintain stability at 2 Hz FSW clock
    self.mrpControl.Ki = -1.0 
    self.mrpControl.integralLimit = 0.0
    self.sim.AddModelToTask(self.fswTaskName, self.mrpControl)

    configData = messaging.VehicleConfigMsgPayload()
    configData.ISCPntB_B = [
        sc.hub.IHubPntBc_B[0][0], 0., 0.,
        0., sc.hub.IHubPntBc_B[1][1], 0.,
        0., 0., sc.hub.IHubPntBc_B[2][2]
    ]
    self.configMsg = messaging.VehicleConfigMsg().write(configData)

    # 3. Flight Software: Reaction Wheel Mapper
    self.rwMotorTorqueObj = rwMotorTorque.rwMotorTorque()
    self.rwMotorTorqueObj.ModelTag = "rw_mapper"
    
    # REQUIRED: Define the 3D identity matrix so the mapper knows it controls all axes
    self.rwMotorTorqueObj.controlAxes_B = [
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0
    ]
    
    self.fswRwConfigMsg = self.rwFactory.getConfigMessage()
    self.rwMotorTorqueObj.rwParamsInMsg.subscribeTo(self.fswRwConfigMsg)
    self.sim.AddModelToTask(self.fswTaskName, self.rwMotorTorqueObj)

    # 4. Message Wiring (Sensors -> Brain -> Mapper -> Physical Motors)
    self.sNav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    self.attError.attNavInMsg.subscribeTo(self.sNav.attOutMsg)
    self.attError.attRefInMsg.subscribeTo(self.attRef.attRefOutMsg)
    self.mrpControl.guidInMsg.subscribeTo(self.attError.attGuidOutMsg)
    self.mrpControl.vehConfigInMsg.subscribeTo(self.configMsg)
    self.rwMotorTorqueObj.vehControlInMsg.subscribeTo(self.mrpControl.cmdTorqueOutMsg)
    self.rwStateEffector.rwMotorCmdInMsg.subscribeTo(self.rwMotorTorqueObj.rwMotorTorqueOutMsg)