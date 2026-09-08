/**
 * state.js — Stage 2
 *
 * Owns the canonical AppState object.
 * All data sources (mock, ingestion, playback) write here via dispatch().
 * All UI components read from here via subscribe().
 *
 * Exposed globals:
 *   window.AppState   — the live state object (read-only from consumers)
 *   window.dispatch   — the single write path
 *   window.subscribe  — register an update callback
 *   window.addEvent   — append a log event + notify
 */

'use strict';

(function () {

  /* ═══════════════════════════════════════════════════════════
     APP STATE — canonical shape.
     Defaults are safe initial values; mock.js / ingestion.js /
     playback.js will overwrite all fields.
  ═══════════════════════════════════════════════════════════ */
  const AppState = {
    // ── Orbit / physics telemetry ───────────────────────────
    collision_prob:    null,       // Pc — null: not produced by gnd_ops, show "—"
    dca_km:            0,          // distance of closest approach (km) — from miss_distance_km
    tca_s:             0,          // seconds to closest approach — DERIVED from tca_iso, see ticker below
    tca_iso:           null,       // absolute UTC ISO timestamp from gnd_ops (time_of_closest_approach)
    fuel_pct:          0,          // propellant reserve (%)
    dv_mag_ms:         0,          // Δv burn magnitude (m/s) — from delta_v_magnitude_mps
    dv_vector:         [0, 0, 0],  // from delta_v_vector_mps
    sat: {
      id: 'Kessler_Sat_1',
      r:  [0, 0, 0],   // position ECI (meters, per core_phy r_eci_m convention)
      v:  [0, 0, 0],   // velocity ECI (m/s)
    },
    debris: {
      id: 'Debris_Obj',
      r:  [0, 0, 0],
      v:  [0, 0, 0],
    },
    orbit_path_sat:    [],  // [[x,y,z] …] — approximated once from live radius, see ingestion.js
    orbit_path_debris: [],

    // ── Drag / ground ML ────────────────────────────────────
    cd:          0,          // drag coefficient
    drag_sigma:  0,          // 1-σ uncertainty on Cd
    ellipsoid: {
      along:   0,   // along-track semi-axis (km)
      cross:   0,   // cross-track semi-axis (km)
      radial:  0,   // radial semi-axis (km)
    },

    // ── Edge-vision / HUD ────────────────────────────────────
    frame_id:              0,
    detections:            [],    // unused in live mode (no detections[] broadcast) — kept for mock/playback compat
    corridor_intersected:  false,
    hud_image:             null,  // "data:image/jpeg;base64,..." from edge_pro, or null

    // ── Decision (Active-Active, from edge_pro decision_engine) ──
    // status:    'EXECUTE_BURN' | 'NO_MANEUVER_REQUIRED' | 'MONITOR_PEER_MANEUVER'
    // go_no_go:  'GO' | 'NO_GO'
    // scores.self / scores.peer shape (from decision_engine.py):
    //   { asset_id, score, factors: { cost_lifetime, cost_delta_v, cost_priority, cost_downtime } }
    //   — LOWER score/cost = more responsible / more likely to execute the burn.
    //   peer is null for SPACECRAFT_VS_DEBRIS conjunctions (solo maneuver,
    //   no active-active negotiation) — see README known-gap note.
    decision: {
      status:       'NO_MANEUVER_REQUIRED',
      go_no_go:     'NO_GO',
      target_asset: '',
      conjunction_type: '',
      scores: { self: null, peer: null },
      weights: { w_lifetime: 0.25, w_delta_v: 0.30, w_priority: 0.25, w_downtime: 0.20 },
    },

    // ── Event log ───────────────────────────────────────────
    // Append-only.  Use addEvent(), not dispatch({ events: … }).
    events: [],

    // ── System / meta ───────────────────────────────────────
    latency_ms:    0,
    mode:          'autonomous',  // 'autonomous' | 'manual'
    burn_approved: false,
    source_status: {
      orbit:  'offline',   // 'connected' | 'playback' | 'error' | 'offline'
      vision: 'offline',
      drag:   'offline',
    },
    is_playback:    true,
    playback_speed: 1,
    last_update_ts: 0,
  };

  /* Criterion → real decision_engine.py factor key mapping.
     decision_engine.py's cost semantics: LOWER cost = more suitable
     to execute the maneuver (more fuel, more lifetime margin, lower
     priority, faster recovery). Default weights below are fallback
     display only — real weights arrive per-packet in decision.weights
     once config-driven values are broadcast; see _updateWeightLabels. */
  window.AOID_WEIGHTS = { fuel: 0.30, lifetime: 0.25, priority: 0.25, downtime: 0.20 };


  /* ═══════════════════════════════════════════════════════════
     SUBSCRIBER REGISTRY
  ═══════════════════════════════════════════════════════════ */
  const subscribers = [];

  function notifySubscribers() {
    for (const fn of subscribers) {
      try { fn(AppState); }
      catch (err) { console.error('[state] subscriber error:', err); }
    }
  }


  /* ═══════════════════════════════════════════════════════════
     DISPATCH — the single write path.
     Performs a shallow-merge for top-level scalars and arrays,
     and a one-level-deep merge for plain objects (e.g. sat, ellipsoid).
     Callers never need to send the full state — just the changed fields.
  ═══════════════════════════════════════════════════════════ */
  function dispatch(patch) {
    for (const key of Object.keys(patch)) {
      const incoming = patch[key];
      const existing  = AppState[key];

      if (
        incoming !== null &&
        typeof incoming === 'object' &&
        !Array.isArray(incoming) &&
        existing !== null &&
        typeof existing === 'object' &&
        !Array.isArray(existing)
      ) {
        // Merge nested plain objects one level deep
        Object.assign(existing, incoming);
      } else {
        AppState[key] = incoming;
      }
    }

    // No client-side composite recompute needed — decision_engine.py
    // already sends the final per-side score directly in scores.self.score
    // / scores.peer.score, and the authoritative decision/status/go_no_go
    // fields, so we just display what arrived.

    AppState.last_update_ts = Date.now();
    notifySubscribers();
  }


  /* ═══════════════════════════════════════════════════════════
     addEvent — append to events[] and notify.
     Use this instead of dispatch() for log entries, so that
     the events array stays append-only and renderers can track
     which entries are new.
  ═══════════════════════════════════════════════════════════ */
  function addEvent(evt) {
    // evt: { ts?: string, tag: string, msg: string, severity: 'nominal'|'warning'|'threat'|'info' }
    AppState.events.push({
      ts:       evt.ts || _nowISO(),
      tag:      evt.tag,
      msg:      evt.msg,
      severity: evt.severity || 'info',
      _id:      AppState.events.length,
    });
    AppState.last_update_ts = Date.now();
    notifySubscribers();
  }

  function _nowISO() {
    const d = new Date();
    return d.toISOString().slice(11, 23); // HH:MM:SS.mmm
  }


  /* ═══════════════════════════════════════════════════════════
     subscribe — register an update callback.
  ═══════════════════════════════════════════════════════════ */
  function subscribe(fn) {
    subscribers.push(fn);
  }


  /* ═══════════════════════════════════════════════════════════
     DOM UPDATE SUBSCRIBER
     Reads from AppState and pushes to all UI elements.
     This is the ONLY place that touches the DOM for data display.
  ═══════════════════════════════════════════════════════════ */

  // Track how many log events we've already rendered
  let _renderedEventCount = 0;

  function _initDOMUpdates() {
    subscribe(function (state) {
      _updateStatusStrip(state);
      _updateSourceDots(state);
      _updateModeBadge(state);
      _updateDecisionMatrix(state);
      _updateLogPanel(state);
      _updateCanvasOverlays(state);
      _updateOverrideButton(state);
    });
  }

  /* ── Status strip ──────────────────────────────────────── */
  function _updateStatusStrip(s) {
    // gnd_ops does not currently compute collision_prob — show "—"
    // rather than a fabricated number until that field exists upstream.
    if (s.collision_prob == null) {
      _setMono('val-collision-prob', '—');
    } else {
      _setMono('val-collision-prob', s.collision_prob.toExponential(2),
               _pcColor(s.collision_prob));
    }
    _setMono('val-dca', s.dca_km.toFixed(2) + ' km', _dcaColor(s.dca_km));
    _setMono('val-tca', _formatTCA(s.tca_s));
    _setMono('val-fuel', s.fuel_pct.toFixed(1) + '%', _fuelColor(s.fuel_pct));
    _setMono('val-dv',  s.dv_mag_ms.toFixed(2) + ' m/s');
    _setMono('val-latency', s.latency_ms.toFixed(0) + ' ms');
  }

  function _setMono(id, text, colorClass) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (colorClass !== undefined) {
      el.className = 'metric-value ' + (colorClass || '');
    }
  }

  function _pcColor(pc) {
    if (pc >= 0.005) return 'status-threat';
    if (pc >= 0.001) return 'status-warning';
    return 'status-nominal';
  }

  function _dcaColor(km) {
    if (km < 0.5)  return 'status-threat';
    if (km < 2.0)  return 'status-warning';
    return 'status-nominal';
  }

  function _fuelColor(pct) {
    if (pct < 20) return 'status-warning';
    return 'status-nominal';
  }

  function _formatTCA(seconds) {
    const sign = seconds < 0 ? 'T+' : 'T\u2212';
    const abs  = Math.abs(Math.round(seconds));
    const m    = Math.floor(abs / 60);
    const s    = abs % 60;
    return `${sign}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  /* ── Source dots ────────────────────────────────────────── */
  const _sourceMap = { orbit: 'src-orbit', vision: 'src-vision', drag: 'src-drag' };

  function _updateSourceDots(s) {
    for (const [key, id] of Object.entries(_sourceMap)) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.className = 'source-dot ' + (s.source_status[key] || 'offline');
    }
  }

  /* ── Mode badge ─────────────────────────────────────────── */
  function _updateModeBadge(s) {
    const badge = document.getElementById('mode-badge');
    if (!badge) return;
    if (s.is_playback) {
      badge.textContent = `● PLAYBACK ${s.playback_speed}×`;
      badge.className = 'mode-badge mode-playback';
    } else {
      badge.textContent = '● LIVE';
      badge.className = 'mode-badge mode-live';
    }
  }

  /* ── Decision matrix ────────────────────────────────────── */
  /* Maps our UI criterion rows to decision_engine.py's real factor keys.
     All are COSTS [0..1] where LOWER = more suitable to execute the burn. */
  const _barDefs = [
    { factorKey: 'cost_delta_v',  weightKey: 'w_delta_v',  idA: 'bar-fuel-a', valA: 'val-fuel-a', idB: 'bar-fuel-b', valB: 'val-fuel-b', wtId: 'wt-fuel' },
    { factorKey: 'cost_lifetime', weightKey: 'w_lifetime', idA: 'bar-life-a', valA: 'val-life-a', idB: 'bar-life-b', valB: 'val-life-b', wtId: 'wt-life' },
    { factorKey: 'cost_priority', weightKey: 'w_priority', idA: 'bar-prio-a', valA: 'val-prio-a', idB: 'bar-prio-b', valB: 'val-prio-b', wtId: 'wt-prio' },
    { factorKey: 'cost_downtime', weightKey: 'w_downtime', idA: 'bar-down-a', valA: 'val-down-a', idB: 'bar-down-b', valB: 'val-down-b', wtId: 'wt-down' },
  ];

  function _updateDecisionMatrix(s) {
    const { scores, status, go_no_go, target_asset, weights, conjunction_type } = s.decision;
    const hasPeer = !!scores.peer;

    _updateWeightLabels(weights);

    for (const def of _barDefs) {
      const selfFactors = scores.self?.factors || {};
      const peerFactors = scores.peer?.factors || {};
      const costSelf = selfFactors[def.factorKey] ?? 0;
      const costPeer = peerFactors[def.factorKey] ?? 0;

      if (!hasPeer) {
        // Solo maneuver (spacecraft vs. passive debris) — no negotiation
        // to visualize. Show self's cost, dim the peer row rather than
        // implying a comparison that didn't happen.
        _setBar(def.idA, def.valA, costSelf, 'bar-accent');
        _setBarNA(def.idB, def.valB);
        continue;
      }

      // LOWER cost = more responsible for the burn — inverse of a typical
      // "higher wins" bar comparison.
      const selfMoreResponsible = costSelf <= costPeer;
      _setBar(def.idA, def.valA, costSelf, selfMoreResponsible ? 'bar-accent' : 'bar-dim');
      _setBar(def.idB, def.valB, costPeer, !selfMoreResponsible ? 'bar-accent' : 'bar-dim');
    }

    const verdictEl = document.getElementById('verdict-text');
    const scoreEl   = document.getElementById('verdict-score');

    const verdictLabels = {
      EXECUTE_BURN: hasPeer
        ? `${target_asset || 'Self'} executes burn (active-active)`
        : `Executing avoidance burn — solo maneuver vs. ${target_asset || 'debris'}`,
      NO_MANEUVER_REQUIRED:  'No maneuver required — corridor clear',
      MONITOR_PEER_MANEUVER: `Monitoring — ${target_asset || 'peer'} maneuvering`,
    };
    const verdictClass = {
      EXECUTE_BURN:          'verdict-warning',
      NO_MANEUVER_REQUIRED:  'verdict-nominal',
      MONITOR_PEER_MANEUVER: 'verdict-info',
    };

    if (verdictEl) {
      verdictEl.textContent = (verdictLabels[status] || status) + `  [${go_no_go}]`;
      verdictEl.className = 'verdict-text ' + (verdictClass[status] || '');
    }
    if (scoreEl) {
      if (scores.self == null) {
        scoreEl.textContent = '—';
      } else if (!hasPeer) {
        scoreEl.textContent = `Self S: ${(scores.self.score ?? 0).toFixed(4)} (solo — ${conjunction_type || 'no peer'})`;
      } else {
        scoreEl.textContent = `Self S: ${(scores.self.score ?? 0).toFixed(4)}  ·  Peer S: ${(scores.peer.score ?? 0).toFixed(4)}`;
      }
    }
  }

  function _updateWeightLabels(weights) {
    if (!weights) return;
    const pct = (w) => Math.round((w ?? 0) * 100) + '%';
    _setText('wt-fuel', pct(weights.w_delta_v));
    _setText('wt-life', pct(weights.w_lifetime));
    _setText('wt-prio', pct(weights.w_priority));
    _setText('wt-down', pct(weights.w_downtime));
  }

  function _setBarNA(barId, valId) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    if (bar) { bar.style.width = '0%'; bar.className = 'bar-fill bar-dim'; }
    if (val) val.textContent = 'N/A';
  }

  function _setBar(barId, valId, score, colorClass) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    if (bar) {
      bar.style.width = (score * 100).toFixed(1) + '%';
      bar.className   = 'bar-fill ' + colorClass;
    }
    if (val) val.textContent = score.toFixed(2);
  }

  /* ── Event log ──────────────────────────────────────────── */
  function _updateLogPanel(s) {
    const body = document.getElementById('event-log-body');
    const countEl = document.getElementById('log-entry-count');
    if (!body) return;

    // Append only new events (append-only array)
    const newEvents = s.events.slice(_renderedEventCount);
    for (const evt of newEvents) {
      const row = _buildLogRow(evt);
      body.appendChild(row);
      // Scroll to bottom
      body.scrollTop = body.scrollHeight;
    }
    _renderedEventCount = s.events.length;

    if (countEl) countEl.textContent = s.events.length + ' entries';
  }

  function _buildLogRow(evt) {
    const row = document.createElement('div');
    row.className = `log-entry log-${evt.severity} log-new`;

    const ts  = document.createElement('span');
    ts.className = 'log-ts';
    ts.textContent = evt.ts;

    const tag = document.createElement('span');
    tag.className = 'log-tag';
    tag.textContent = evt.tag;

    const msg = document.createElement('span');
    msg.className = 'log-msg';
    msg.textContent = evt.msg;

    row.appendChild(ts);
    row.appendChild(tag);
    row.appendChild(msg);
    return row;
  }

  /* ── Canvas overlays (text labels on top of canvases) ──── */
  function _updateCanvasOverlays(s) {
    _setText('traj-objects',   `${s.sat.id} · ${s.debris.id}`);
    _setText('annot-tca-value', _formatTCA(s.tca_s));
    _setText('raw-frame-id',   `Frame #${s.frame_id}`);

    // No detections[] array is broadcast live — corridor_intersected +
    // decision status is the real signal from edge_pro instead.
    _setText('proc-detections', s.corridor_intersected ? 'Corridor: HIT' : 'Corridor: clear');
    _setText('proc-conf', s.hud_image ? 'Live HUD' : '');

    // The raw panel never receives real video in this pipeline — only
    // the finished HUD image reaches the browser — so it always reads
    // as simulated. Only the processed panel becomes genuinely live.
    _setText('raw-status',  '● SIMULATED FEED');
    _setText('proc-status', s.hud_image ? '● LIVE HUD' : '● SIMULATED FEED');
  }

  function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  /* ── Override switch sync ───────────────────────────────── */
  function _updateOverrideButton(s) {
    if (typeof window.setOverrideMode === 'function') {
      window.setOverrideMode(s.mode === 'manual');
    }
  }

  /* ── Log clear button ────────────────────────────────────── */
  function _initLogClear() {
    const btn = document.getElementById('btn-clear-log');
    if (!btn) return;
    btn.addEventListener('click', () => {
      AppState.events.length = 0;
      _renderedEventCount = 0;
      const body = document.getElementById('event-log-body');
      if (body) body.innerHTML = '';
      const countEl = document.getElementById('log-entry-count');
      if (countEl) countEl.textContent = '0 entries';
    });
  }


  /* ── TCA countdown ticker ─────────────────────────────────
     gnd_ops sends an ABSOLUTE UTC timestamp (time_of_closest_approach),
     not a live-decrementing seconds value. Compute tca_s against wall
     clock every tick so the status strip still counts down smoothly.
     Playback mode already sends tca_s directly per-frame and doesn't
     set tca_iso, so this is a no-op during playback.
  ─────────────────────────────────────────────────────────── */
  function _startTcaTicker() {
    setInterval(() => {
      if (!AppState.tca_iso) return;
      const target = new Date(AppState.tca_iso).getTime();
      if (Number.isNaN(target)) return;
      const secondsLeft = (target - Date.now()) / 1000;
      dispatch({ tca_s: secondsLeft });
    }, 500);
  }


  /* ═══════════════════════════════════════════════════════════
     BOOT
  ═══════════════════════════════════════════════════════════ */
  function _boot() {
    _initDOMUpdates();
    _initLogClear();
    _startTcaTicker();
    console.info('[state] ready.');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }


  /* ═══════════════════════════════════════════════════════════
     GLOBAL EXPORTS
  ═══════════════════════════════════════════════════════════ */
  window.AppState  = AppState;
  window.dispatch  = dispatch;
  window.subscribe = subscribe;
  window.addEvent  = addEvent;

})();
