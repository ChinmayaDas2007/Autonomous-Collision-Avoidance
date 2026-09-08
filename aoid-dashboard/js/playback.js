/**
 * playback.js — Stage 3
 *
 * Drives playback of pre-recorded telemetry log files or embedded scenarios.
 * Restructured narrative: reveals backward from TCA zero-point to T-15m.
 * Emits sequential state updates through window.dispatch() and window.addEvent().
 * Supports 1x, 2x, and 4x playback speeds.
 *
 * Exposed globals:
 *   window.setPlaybackSpeed(speed) — change playback speed (1, 2, 4)
 */

'use strict';

(function () {

  // Embedded fallback scenario dataset (reveals backward from TCA zero-point)
  const EMBEDDED_SCENARIO = [
    {
      "frame_id": 41225, "tca_s": 0, "collision_prob": 0.0087, "dca_km": 0.32, "fuel_pct": 60.8,
      "dv_mag_ms": 0.00, "dv_vector": [0.00, 0.00, 0.00],
      "sat": { "id": "SAT-A", "r": [4211.0, 3120.7, 4520.6], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4211.3, 3120.8, 4520.7], "v": [4.12, -5.10, -3.15] },
      "cd": 2.31, "drag_sigma": 0.11, "ellipsoid": { "along": 1.70, "cross": 0.42, "radial": 0.26 },
      "detections": [{ "cx": 0.493, "cy": 0.389, "w": 0.070, "h": 0.040, "conf": 0.96 }],
      "decision": { "winner": "SAT-A", "scores": { "sat_a": { "fuel": 0.78, "lifetime": 0.91, "priority": 0.60, "downtime": 0.85 }, "sat_b": { "fuel": 0.44, "lifetime": 0.55, "priority": 0.82, "downtime": 0.39 } } },
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 1/6: TCA Zero-Point reached. Satellites co-located at DCA 0.32 km. Pc = 8.7e-3.", "severity": "threat" }
    },
    {
      "frame_id": 41220, "tca_s": 120, "collision_prob": 0.0087, "dca_km": 0.32, "fuel_pct": 62.2,
      "dv_mag_ms": 1.20, "dv_vector": [0.12, -0.45, 0.88],
      "sat": { "id": "SAT-A", "r": [4211.0, 3120.7, 4520.6], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4211.3, 3120.8, 4520.7], "v": [4.12, -5.10, -3.15] },
      "cd": 2.31, "drag_sigma": 0.11, "ellipsoid": { "along": 1.70, "cross": 0.42, "radial": 0.26 },
      "detections": [{ "cx": 0.490, "cy": 0.386, "w": 0.068, "h": 0.039, "conf": 0.94 }],
      "decision": { "winner": "SAT-A", "scores": { "sat_a": { "fuel": 0.78, "lifetime": 0.91, "priority": 0.60, "downtime": 0.85 }, "sat_b": { "fuel": 0.44, "lifetime": 0.55, "priority": 0.82, "downtime": 0.39 } } },
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 2/6: Tracing Δv maneuver execution (Δv = 1.20 m/s impulse vector active).", "severity": "warning" }
    },
    {
      "frame_id": 41215, "tca_s": 300, "collision_prob": 0.0076, "dca_km": 0.58, "fuel_pct": 62.3,
      "dv_mag_ms": 1.20, "dv_vector": [0.12, -0.45, 0.88],
      "sat": { "id": "SAT-A", "r": [4210.9, 3120.6, 4520.5], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4211.4, 3120.8, 4520.7], "v": [4.12, -5.10, -3.15] },
      "cd": 2.30, "drag_sigma": 0.11, "ellipsoid": { "along": 1.65, "cross": 0.40, "radial": 0.25 },
      "detections": [{ "cx": 0.485, "cy": 0.382, "w": 0.066, "h": 0.038, "conf": 0.92 }],
      "decision": { "winner": "SAT-A", "scores": { "sat_a": { "fuel": 0.78, "lifetime": 0.91, "priority": 0.60, "downtime": 0.85 }, "sat_b": { "fuel": 0.44, "lifetime": 0.55, "priority": 0.82, "downtime": 0.39 } } },
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 3/6: Matrix selection node reached — SAT-A composite score 0.777.", "severity": "warning" }
    },
    {
      "frame_id": 41210, "tca_s": 480, "collision_prob": 0.0052, "dca_km": 0.92, "fuel_pct": 62.3,
      "dv_mag_ms": 0.00, "dv_vector": [0.00, 0.00, 0.00],
      "sat": { "id": "SAT-A", "r": [4210.8, 3120.5, 4520.4], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4211.7, 3121.0, 4521.0], "v": [4.12, -5.10, -3.15] },
      "cd": 2.27, "drag_sigma": 0.10, "ellipsoid": { "along": 1.55, "cross": 0.36, "radial": 0.23 },
      "detections": [{ "cx": 0.480, "cy": 0.378, "w": 0.064, "h": 0.037, "conf": 0.90 }],
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 4/6: Unwinding threat trajectory — DCA expanding backward to 0.92 km.", "severity": "info" }
    },
    {
      "frame_id": 41205, "tca_s": 660, "collision_prob": 0.0028, "dca_km": 1.40, "fuel_pct": 62.3,
      "dv_mag_ms": 0.00, "dv_vector": [0.00, 0.00, 0.00],
      "sat": { "id": "SAT-A", "r": [4210.7, 3120.4, 4520.3], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4212.1, 3121.3, 4521.4], "v": [4.12, -5.10, -3.15] },
      "cd": 2.24, "drag_sigma": 0.09, "ellipsoid": { "along": 1.48, "cross": 0.33, "radial": 0.21 },
      "detections": [{ "cx": 0.475, "cy": 0.373, "w": 0.062, "h": 0.036, "conf": 0.88 }],
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 5/6: Atmospheric drag uncertainty origin point (Cd = 2.24 ± 0.09).", "severity": "info" }
    },
    {
      "frame_id": 41200, "tca_s": 900, "collision_prob": 0.0010, "dca_km": 2.14, "fuel_pct": 62.4,
      "dv_mag_ms": 0.00, "dv_vector": [0.00, 0.00, 0.00],
      "sat": { "id": "SAT-A", "r": [4210.5, 3120.2, 4520.1], "v": [-3.85, 5.92, 2.41] },
      "debris": { "id": "DEB-7", "r": [4212.6, 3121.8, 4521.9], "v": [4.12, -5.10, -3.15] },
      "cd": 2.21, "drag_sigma": 0.08, "ellipsoid": { "along": 1.40, "cross": 0.30, "radial": 0.20 },
      "detections": [{ "cx": 0.470, "cy": 0.370, "w": 0.060, "h": 0.035, "conf": 0.85 }],
      "event": { "tag": "RECONSTRUCT", "msg": "RECONSTRUCTION BEAT 6/6: Initial nominal tracking state reached prior to conjunction alert.", "severity": "nominal" }
    }
  ];

  let frames = EMBEDDED_SCENARIO;
  let currentFrameIdx = 0;
  let currentSpeed = 1;
  let timerId = null;
  let _orbitPathsDispatched = false;

  /**
   * generateOrbitArc — 72-point orbit ellipse in ECI coords (km).
   * playback_log.json only carries per-frame sat/debris POSITIONS
   * (single points), never the full arc — but canvas.js's trajectory
   * renderer needs orbit_path_sat / orbit_path_debris (arrays of
   * points) to draw the orbit lines. Same math as mock.js so it lines
   * up visually with the ~6871km-radius positions in the log.
   * Dispatched once at boot, not per-frame — it's a static backdrop,
   * the moving markers come from the live per-frame sat/debris data.
   */
  function generateOrbitArc(radiusKm, incDeg, raanDeg, argPerDeg, phaseOffsetRad = 0) {
    const points = [];
    const N = 72;
    const inc = (incDeg * Math.PI) / 180;
    const raan = (raanDeg * Math.PI) / 180;
    const argP = (argPerDeg * Math.PI) / 180;

    for (let i = 0; i < N; i++) {
      const u = (i / N) * 2 * Math.PI + phaseOffsetRad;
      const xp = radiusKm * Math.cos(u);
      const yp = radiusKm * Math.sin(u);
      const x1 = xp * Math.cos(argP) - yp * Math.sin(argP);
      const y1 = xp * Math.sin(argP) + yp * Math.cos(argP);
      const x = x1 * Math.cos(raan) - y1 * Math.cos(inc) * Math.sin(raan);
      const y = x1 * Math.sin(raan) + y1 * Math.cos(inc) * Math.cos(raan);
      const z = y1 * Math.sin(inc);
      points.push([x, y, z]);
    }
    return points;
  }

  function _dispatchOrbitPathsOnce() {
    if (_orbitPathsDispatched) return;
    _orbitPathsDispatched = true;
    window.dispatch({
      orbit_path_sat:    generateOrbitArc(6871.0, 51.6, 120.0, 45.0),
      orbit_path_debris: generateOrbitArc(6871.8, 52.4, 119.5, 48.0),
    });
  }

  /**
   * Load JSON log if fetch is available
   */
  async function loadLog() {
    try {
      const resp = await fetch('data/playback_log.json');
      if (resp.ok) {
        const loaded = await resp.json();
        if (Array.isArray(loaded) && loaded.length > 0) {
          frames = loaded;
          console.info('[playback] Loaded external playback_log.json with', frames.length, 'frames.');
        }
      }
    } catch {
      console.info('[playback] Using embedded fallback scenario dataset.');
    }
    startPlayback();
  }

  function startPlayback() {
    if (timerId) clearInterval(timerId);

    // Static orbit-arc backdrop — dispatched once, independent of
    // the per-frame ticker (see generateOrbitArc comment above).
    _dispatchOrbitPathsOnce();

    // Initial state setup for playback mode
    window.dispatch({
      is_playback: true,
      playback_speed: currentSpeed,
      source_status: { orbit: 'playback', vision: 'playback', drag: 'playback' }
    });

    const intervalMs = 1800 / currentSpeed;
    timerId = setInterval(stepFrame, intervalMs);
  }

  function stepFrame() {
    if (!frames || frames.length === 0) return;

    const f = frames[currentFrameIdx];
    if (f) {
      // Patch state
      const patch = Object.assign({}, f);
      delete patch.event; // Event handled separately via addEvent
      patch.is_playback = true;
      patch.playback_speed = currentSpeed;
      patch.source_status = { orbit: 'playback', vision: 'playback', drag: 'playback' };

      window.dispatch(patch);

      if (f.event) {
        window.addEvent(f.event);
      }
    }

    currentFrameIdx = (currentFrameIdx + 1) % frames.length;
  }

  /**
   * Set playback speed (1, 2, 4)
   */
  function setPlaybackSpeed(speed) {
    const s = parseInt(speed, 10);
    if ([1, 2, 4].includes(s)) {
      currentSpeed = s;
      window.dispatch({ playback_speed: currentSpeed });
      startPlayback();
    }
  }

  // Boot playback engine
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadLog);
  } else {
    loadLog();
  }

  // Global export
  window.setPlaybackSpeed = setPlaybackSpeed;

})();
