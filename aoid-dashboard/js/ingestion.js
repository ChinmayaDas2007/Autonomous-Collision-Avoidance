/**
 * ingestion.js — LIVE INTEGRATION (updated against real teammate code)
 *
 * WebSocket listeners for the three DashboardBroadcaster sources that
 * ship inside core_phy (fsw_server.py), edge_pro (payload_manager.py),
 * and gnd_ops (ground_ai_node.py). Ports are hardcoded on their side
 * (8001/8002/8003) — only the HOST differs per network_config.json.
 *
 * Real per-laptop IPs from network_config.json:
 *   LAPTOP_1_CORE_IP  = 192.168.1.10  → core_phy   → orbit  → :8001
 *   LAPTOP_2_EDGE_IP  = 192.168.1.11  → edge_pro   → vision → :8002
 *   LAPTOP_3_GROUND_IP= 192.168.1.12  → gnd_ops    → drag   → :8003
 * If the venue wifi changes these, update HOSTS below — nothing else
 * needs to change. If running everything on one laptop, set all three
 * to 'localhost'.
 *
 * Exposed globals: none (all side-effects via window.dispatch).
 */

'use strict';

(function () {

  /* ═══════════════════════════════════════════════════════════
     HOST CONFIGURATION — edit these three lines only
  ═══════════════════════════════════════════════════════════ */
  const HOSTS = {
    orbit:  '172.16.130.98',   // core_phy   (fsw_server.py)
    vision: '172.16.130.98',   // edge_pro   (payload_manager.py)
    drag:   '192.168.1.12',   // gnd_ops    (ground_ai_node.py)
  };

  const SOURCES = {
    orbit:  { url: `ws://${HOSTS.orbit}:8001`,  key: 'orbit'  },
    vision: { url: `ws://${HOSTS.vision}:8002`, key: 'vision' },
    drag:   { url: `ws://${HOSTS.drag}:8003`,   key: 'drag'   },
  };

  const RECONNECT_DELAY_MS = 3000;


  /* ═══════════════════════════════════════════════════════════
     ONE-TIME ORBIT ARC GENERATION
     core_phy only ever broadcasts a single position point per
     tick (sat.r / debris.r), never a full arc — but the trajectory
     canvas needs orbit_path_sat / orbit_path_debris (point arrays)
     to draw the ellipse lines. Approximate a circular arc from the
     first live position's radius, once, purely as a visual backdrop.
     The real moving markers still come from live per-tick data.
  ═══════════════════════════════════════════════════════════ */
  let _orbitPathsDispatched = false;

  function _magnitude(v) {
    return Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
  }

  function _approxCircularArc(radius, phaseOffset = 0) {
    const points = [];
    const N = 72;
    for (let i = 0; i < N; i++) {
      const u = (i / N) * 2 * Math.PI + phaseOffset;
      points.push([radius * Math.cos(u), radius * Math.sin(u), 0]);
    }
    return points;
  }

  function _dispatchApproxOrbitPathsOnce(satR, debrisR) {
    if (_orbitPathsDispatched) return;
    if (!satR || !debrisR) return;
    _orbitPathsDispatched = true;
    window.dispatch({
      orbit_path_sat:    _approxCircularArc(_magnitude(satR)),
      orbit_path_debris: _approxCircularArc(_magnitude(debrisR), 0.15),
    });
  }


  /* ═══════════════════════════════════════════════════════════
     PACKET NORMALIZERS — matched to real broadcast payloads
  ═══════════════════════════════════════════════════════════ */
  const normalizers = {

    /**
     * From core_phy/fsw_server.py DashboardBroadcaster (port 8001):
     *   { type:"orbit", ts, sat:{id,r,v}, debris:{id,r,v} }
     * r/v are in METERS / m/s (r_eci_m convention) — NOT km like the
     * original placeholder schema assumed. No dca_km / collision_prob /
     * dv_vector on this channel; those come from drag (8003) and
     * vision (8002) respectively.
     */
    orbit(pkt) {
      if (!pkt.sat || !pkt.debris) return null;

      _dispatchApproxOrbitPathsOnce(pkt.sat.r, pkt.debris.r);

      return {
        sat: {
          id: pkt.sat.id ?? 'Kessler_Sat_1',
          r:  pkt.sat.r  ?? [0, 0, 0],
          v:  pkt.sat.v  ?? [0, 0, 0],
        },
        debris: {
          id: pkt.debris.id ?? 'Debris_Obj',
          r:  pkt.debris.r  ?? [0, 0, 0],
          v:  pkt.debris.v  ?? [0, 0, 0],
        },
      };
    },

    /**
     * From edge_pro/payload_manager.py DashboardBroadcaster (port 8002):
     *   { type:"vision", ts, frame_id, corridor_intersected,
     *     hud_frame_path (base64 data URI "data:image/jpeg;base64,..."),
     *     decision, decision_status, target_asset,
     *     delta_v_vector_mps, delta_v_magnitude_mps,
     *     scores:{ self:{asset_id,score,factors:{cost_lifetime,cost_delta_v,
     *                                             cost_priority,cost_downtime}},
     *              peer: same shape, or {} if no active-active peer } }
     * Confirmed against decision_engine.py: scores.self/.peer are ONLY
     * populated for conjunction_type ACTIVE_ACTIVE_SPACECRAFT. For
     * SPACECRAFT_VS_DEBRIS (the likely demo case — see test_three_node_e2e.py's
     * Debris_Obj_8492 scenario), scoring_audit uses local_asset/local_score/
     * local_factors instead, which payload_manager.py's current broadcast
     * code does NOT forward under self/peer — so scores arrives empty in
     * that case. See README §"Known gap" for the 1-line python patch to
     * send you. The UI below degrades gracefully (shows "—" / solo-burn
     * framing) rather than misrepresenting empty data as a real score.
     * No detections[] array is sent — the HUD image already has the
     * corridor overlay baked in server-side, so the dashboard displays
     * it directly instead of re-drawing bounding boxes client-side.
     */
    vision(pkt) {
      const patch = {
        frame_id:             pkt.frame_id ?? 0,
        corridor_intersected: !!pkt.corridor_intersected,
        dv_vector:            pkt.delta_v_vector_mps     ?? [0, 0, 0],
        dv_mag_ms:            pkt.delta_v_magnitude_mps  ?? 0,
        decision: {
          status:        pkt.decision        ?? 'NO_MANEUVER_REQUIRED',
          go_no_go:      pkt.decision_status ?? 'NO_GO',
          target_asset:  pkt.target_asset    ?? '',
        },
      };
      // Pass the real nested shape through as-is — state.js interprets it
      // (score direction, factor keys, solo-vs-active-active) since that's
      // where the display logic lives, not the wire normalizer.
      if (pkt.scores) {
        const hasSelf = pkt.scores.self && Object.keys(pkt.scores.self).length > 0;
        const hasPeer = pkt.scores.peer && Object.keys(pkt.scores.peer).length > 0;
        patch.decision.scores = {
          self: hasSelf ? pkt.scores.self : null,
          peer: hasPeer ? pkt.scores.peer : null,
        };
      }
      if (pkt.weights) {
        patch.decision.weights = pkt.weights;
      }
      if (pkt.hud_frame_path) {
        patch.hud_image = pkt.hud_frame_path;
      }
      return patch;
    },

    /**
     * From gnd_ops/ground_ai_node.py DashboardBroadcaster (port 8003):
     *   { type:"drag", ts, cd, drag_sigma, ellipsoid:{along,cross,radial},
     *     time_of_closest_approach (ISO-8601 UTC string), miss_distance_km }
     * No collision_prob field exists anywhere in ground_ai_node.py —
     * the status strip shows "—" for that metric rather than a fake
     * number. If this changes, add the field here.
     */
    drag(pkt) {
      return {
        cd:         pkt.cd         ?? 0,
        drag_sigma: pkt.drag_sigma ?? 0,
        ellipsoid: {
          along:  pkt.ellipsoid?.along  ?? 0,
          cross:  pkt.ellipsoid?.cross  ?? 0,
          radial: pkt.ellipsoid?.radial ?? 0,
        },
        dca_km:  pkt.miss_distance_km ?? 0,
        tca_iso: pkt.time_of_closest_approach || null,
      };
    },
  };


  /* ═══════════════════════════════════════════════════════════
     CONNECTION MANAGER — unchanged from Stage 2
  ═══════════════════════════════════════════════════════════ */
  function _connect(sourceKey) {
    const cfg = SOURCES[sourceKey];
    if (!cfg) return;

    let ws;
    try {
      ws = new WebSocket(cfg.url);
    } catch (err) {
      console.warn(`[ingestion:${sourceKey}] cannot create WebSocket:`, err.message);
      _setStatus(sourceKey, 'error');
      return;
    }

    ws.addEventListener('open', () => {
      console.info(`[ingestion:${sourceKey}] connected to ${cfg.url}`);
      _setStatus(sourceKey, 'connected');
      window.dispatch({ is_playback: false });
      window.addEvent({
        tag:      sourceKey.toUpperCase(),
        msg:      `WebSocket connected to ${cfg.url}.`,
        severity: 'nominal',
      });
    });

    ws.addEventListener('message', (ev) => {
      let pkt;
      try { pkt = JSON.parse(ev.data); }
      catch { return; }

      const routeKey = (pkt.type && normalizers[pkt.type]) ? pkt.type : sourceKey;
      const normalizer = normalizers[routeKey];
      if (!normalizer) return;

      const patch = normalizer(pkt);
      if (patch) {
        patch.latency_ms = Date.now() - (pkt.ts * 1000 || Date.now());
        window.dispatch(patch);

        if (routeKey === 'vision' && patch.decision && patch.decision.status !== 'NO_MANEUVER_REQUIRED') {
          window.addEvent({
            tag:      'DECISION',
            msg:      `${patch.decision.status} — ${patch.decision.go_no_go} (target: ${patch.decision.target_asset || 'n/a'})`,
            severity: patch.decision.status === 'EXECUTE_BURN' ? 'warning' : 'info',
          });
        }
      }
    });

    ws.addEventListener('error', () => {
      console.warn(`[ingestion:${sourceKey}] socket error — will retry.`);
    });

    ws.addEventListener('close', (ev) => {
      _setStatus(sourceKey, 'error');
      console.info(`[ingestion:${sourceKey}] closed (clean=${ev.wasClean}). Reconnecting in ${RECONNECT_DELAY_MS}ms.`);
      if (!_unloading) {
        setTimeout(() => _connect(sourceKey), RECONNECT_DELAY_MS);
      }
    });
  }

  function _setStatus(key, status) {
    window.dispatch({ source_status: { [key]: status } });
  }

  let _unloading = false;
  window.addEventListener('beforeunload', () => { _unloading = true; });


  function _boot() {
    for (const key of Object.keys(SOURCES)) {
      _connect(key);
    }
    console.info('[ingestion] WebSocket connections initiated:', SOURCES);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }

})();
