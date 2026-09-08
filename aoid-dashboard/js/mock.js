/**
 * mock.js — Stage 2
 *
 * Provides a rich, dynamic mock dataset for AOID Mission Control.
 * Populates all AppState fields so the entire UI renders completely
 * with zero live WebSocket backend connections.
 *
 * Exposed globals: none (all side-effects via window.dispatch / window.addEvent).
 */

'use strict';

(function () {

  /**
   * Helper: Generate a 72-point 3D orbit arc in ECI coordinates (km).
   */
  function generateOrbitArc(radiusKm, incDeg, raanDeg, argPerDeg, phaseOffsetRad = 0) {
    const points = [];
    const N = 72;
    const inc = (incDeg * Math.PI) / 180;
    const raan = (raanDeg * Math.PI) / 180;
    const argP = (argPerDeg * Math.PI) / 180;

    for (let i = 0; i < N; i++) {
      const u = (i / N) * 2 * Math.PI + phaseOffsetRad; // true anomaly / argument of latitude
      // Orbital plane position
      const xp = radiusKm * Math.cos(u);
      const yp = radiusKm * Math.sin(u);

      // Rotate by argument of perigee, inclination, and RAAN
      const x1 = xp * Math.cos(argP) - yp * Math.sin(argP);
      const y1 = xp * Math.sin(argP) + yp * Math.cos(argP);

      const x = x1 * Math.cos(raan) - y1 * Math.cos(inc) * Math.sin(raan);
      const y = x1 * Math.sin(raan) + y1 * Math.cos(inc) * Math.cos(raan);
      const z = y1 * Math.sin(inc);

      points.push([x, y, z]);
    }
    return points;
  }

  // Pre-generate realistic 3D orbits for SAT-A (LEO ~6800km) and DEB-7 (crossing orbit)
  const satOrbit = generateOrbitArc(6871, 51.6, 120.0, 45.0);
  const debOrbit = generateOrbitArc(6871.8, 52.4, 119.5, 48.0);

  // Initial telemetry snapshot
  let currentTca = 847; // T-14:07
  let currentFrame = 41209;

  const mockData = {
    collision_prob: 0.0034,
    dca_km:         0.42,
    tca_s:          currentTca,
    fuel_pct:       62.4,
    dv_mag_ms:      1.20,
    dv_vector:      [0.12, -0.45, 0.88],

    sat: {
      id: 'SAT-A',
      r:  [4210.5, 3120.2, 4520.1],
      v:  [-3.85, 5.92, 2.41],
    },
    debris: {
      id: 'DEB-7',
      r:  [4210.8, 3120.5, 4520.3],
      v:  [4.12, -5.10, -3.15],
    },

    orbit_path_sat:    satOrbit,
    orbit_path_debris: debOrbit,

    cd:          2.21,
    drag_sigma:  0.08,
    ellipsoid: {
      along:  1.40,
      cross:  0.30,
      radial: 0.20,
    },

    frame_id: currentFrame,
    detections: [
      { cx: 0.485, cy: 0.382, w: 0.065, h: 0.038, conf: 0.912 }
    ],

    decision: {
      winner:      'SAT-A',
      composite_a: 0,
      composite_b: 0,
      scores: {
        sat_a: { fuel: 0.78, lifetime: 0.91, priority: 0.60, downtime: 0.85 },
        sat_b: { fuel: 0.44, lifetime: 0.55, priority: 0.82, downtime: 0.39 },
      },
    },

    latency_ms: 24,
    mode: 'autonomous',
    burn_approved: false,
    source_status: {
      orbit:  'playback',
      vision: 'playback',
      drag:   'playback',
    },
    is_playback:    true,
    playback_speed: 1,
  };

  // Initial event log history
  const initialEvents = [
    { tag: 'SYS',    msg: 'AOID Mission Control v2.4 initialized. Telemetry channels online.', severity: 'info' },
    { tag: 'ORBIT',  msg: 'Conjunction detected: SAT-A vs DEB-7. Initial DCA: 2.14 km.', severity: 'nominal' },
    { tag: 'DRAG',   msg: 'Atmosphere drag ML model active. Cd: 2.21 ± 0.08.', severity: 'info' },
    { tag: 'VISION', msg: 'YOLOv8-space edge vision stream locked on frame #41200.', severity: 'info' },
    { tag: 'WARN',   msg: 'Pc cross threshold 1.0e-3 (Current: 3.4e-3). DCA closing to 0.42 km.', severity: 'warning' },
    { tag: 'MATRIX', msg: 'Active-Active evaluation complete. Winner: SAT-A (Score: 0.777).', severity: 'nominal' }
  ];

  /**
   * Boot function to populate mock state
   */
  function initMock() {
    if (typeof window.dispatch !== 'function') {
      console.error('[mock] state.js dispatch function missing!');
      return;
    }

    // Dispatch full mock snapshot
    window.dispatch(mockData);

    // Populate initial log entries
    initialEvents.forEach(evt => window.addEvent(evt));

    console.info('[mock] Mock telemetry snapshot loaded into AppState.');

    // Start live simulation ticker (1s update loop)
    setInterval(() => {
      currentTca = Math.max(0, currentTca - 1);
      currentFrame++;

      // Small subtle jitter to optical bounding box for realistic animation
      const jitterX = (Math.random() - 0.5) * 0.004;
      const jitterY = (Math.random() - 0.5) * 0.004;

      window.dispatch({
        tca_s: currentTca,
        frame_id: currentFrame,
        latency_ms: 20 + Math.floor(Math.random() * 12),
        detections: [
          { cx: 0.485 + jitterX, cy: 0.382 + jitterY, w: 0.065, h: 0.038, conf: 0.90 + Math.random() * 0.03 }
        ]
      });
    }, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMock);
  } else {
    initMock();
  }

})();
