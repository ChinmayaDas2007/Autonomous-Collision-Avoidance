/**
 * ui.js — Stage 1 only
 *
 * Scope: pure UI interaction with no data dependency.
 *   - Override switch toggle (AUTONOMOUS ↔ MANUAL)
 *   - Playback speed button group
 *   - Canvas resize observer (hooks canvas elements to their
 *     container size so Stage 2 renderers get correct pixel dims)
 *
 * Stage 2 will add: state.js, ingestion.js, mock.js, canvas.js
 * Stage 3 will add: playback.js
 */

'use strict';

(function () {

  /* ── OVERRIDE SWITCH ──────────────────────────────────────────
     Toggles between AUTONOMOUS and MANUAL mode.
     In MANUAL: burn-actions div slides in via CSS transition.
     This is the ONE deliberate animation in the UI.
  ─────────────────────────────────────────────────────────────── */
  const switchHousing    = document.getElementById('switch-housing');
  const burnActions      = document.getElementById('burn-actions');
  const btnApprove       = document.getElementById('btn-approve-burn');
  const btnHold          = document.getElementById('btn-hold-burn');
  const labelAuto        = document.getElementById('label-auto');
  const labelManual      = document.getElementById('label-manual');
  const overrideStatusLbl = document.getElementById('override-status-label');

  let isManual = false;

  /**
   * setMode — apply autonomous/manual state to all related DOM nodes.
   * Designed to be callable from Stage 2 state.js as well
   * (e.g. window.setOverrideMode(true)).
   *
   * @param {boolean} manual - true = MANUAL, false = AUTONOMOUS
   */
  function setMode(manual, syncState = true) {
    isManual = manual;
    switchHousing.setAttribute('aria-checked', manual ? 'true' : 'false');

    if (manual) {
      labelManual.classList.add('label-active');
      labelAuto.classList.remove('label-active');
      burnActions.classList.add('burn-visible');
      burnActions.setAttribute('aria-hidden', 'false');
      btnApprove.disabled = false;
      btnHold.disabled    = false;
      overrideStatusLbl.textContent = 'Manual mode — awaiting command';
    } else {
      labelAuto.classList.add('label-active');
      labelManual.classList.remove('label-active');
      burnActions.classList.remove('burn-visible');
      burnActions.setAttribute('aria-hidden', 'true');
      btnApprove.disabled = true;
      btnHold.disabled    = true;
      overrideStatusLbl.textContent = 'Autonomous mode active';
    }

    if (syncState && typeof window.dispatch === 'function' && window.AppState && window.AppState.mode !== (manual ? 'manual' : 'autonomous')) {
      window.dispatch({ mode: manual ? 'manual' : 'autonomous' });
    }
  }

  switchHousing.addEventListener('click', () => setMode(!isManual));

  switchHousing.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      setMode(!isManual);
    }
  });

  // Approve / Hold manual actions
  btnApprove.addEventListener('click', () => {
    console.info('[AOID] Burn approved by operator.');
    if (typeof window.dispatch === 'function') {
      window.dispatch({ burn_approved: true });
    }
    if (typeof window.addEvent === 'function') {
      window.addEvent({
        tag: 'MANUAL',
        msg: 'OPERATOR OVERRIDE: Maneuver burn approved manually.',
        severity: 'warning'
      });
    }
  });

  btnHold.addEventListener('click', () => {
    console.info('[AOID] Burn held by operator.');
    if (typeof window.dispatch === 'function') {
      window.dispatch({ burn_approved: false });
    }
    if (typeof window.addEvent === 'function') {
      window.addEvent({
        tag: 'MANUAL',
        msg: 'OPERATOR OVERRIDE: Maneuver burn placed on HOLD.',
        severity: 'threat'
      });
    }
  });

  // Initialise to autonomous
  setMode(false);

  // Expose for Stage 2 use
  window.setOverrideMode = setMode;


  /* ── PLAYBACK SPEED BUTTONS ───────────────────────────────────
     Manages aria-pressed state on the 1× / 2× / 4× buttons.
     Actual speed logic lives in Stage 3 playback.js.
  ─────────────────────────────────────────────────────────────── */
  const speedButtons = document.querySelectorAll('.speed-btn');

  speedButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const speed = Number(btn.dataset.speed);

      speedButtons.forEach((b) => b.setAttribute('aria-pressed', 'false'));
      btn.setAttribute('aria-pressed', 'true');

      // Stage 3 hook
      if (typeof window.setPlaybackSpeed === 'function') {
        window.setPlaybackSpeed(speed);
      }

      console.info(`[AOID] Playback speed set to ${speed}×`);
    });
  });


  /* ── CANVAS RESIZE OBSERVER ───────────────────────────────────
     Keeps canvas pixel dimensions in sync with CSS layout size.
     Stage 2 canvas.js registers per-canvas redraw callbacks via
     window.registerCanvasRedraw(canvasId, fn).
     This module fires those callbacks on resize so renderers
     always draw at the correct physical resolution.
  ─────────────────────────────────────────────────────────────── */
  const canvasIds = [
    'canvas-trajectory',
    'canvas-raw',
    'canvas-processed',
  ];

  // Registry: canvasId → redraw callback (populated by canvas.js in Stage 2)
  const redrawRegistry = {};

  /**
   * Registers a redraw callback for a canvas.
   * Called by canvas.js once per canvas during Stage 2 init.
   *
   * @param {string}   canvasId - the canvas element ID
   * @param {Function} fn       - called with (canvas, width, height) on resize
   */
  window.registerCanvasRedraw = function (canvasId, fn) {
    redrawRegistry[canvasId] = fn;
  };

  /**
   * Syncs a canvas element's pixel buffer to its CSS display size.
   * Fires the registered redraw callback if one exists.
   *
   * @param {HTMLCanvasElement} canvas
   */
  function syncCanvas(canvas) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    if (w === 0 || h === 0) return;

    if (canvas.width !== w || canvas.height !== h) {
      canvas.width  = w;
      canvas.height = h;
    }

    const fn = redrawRegistry[canvas.id];
    if (typeof fn === 'function') {
      fn(canvas, w, h);
    }
  }

  // ResizeObserver fires on initial layout and any subsequent resize
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      canvasIds.forEach((id) => {
        const canvas = document.getElementById(id);
        if (canvas) syncCanvas(canvas);
      });
    });

    // Observe the parent panels (not the canvases themselves,
    // since canvas dimensions are set by JS, not CSS)
    const observeTargets = [
      'trajectory-body',
      'panel-raw',
      'panel-processed',
    ];

    observeTargets.forEach((id) => {
      const el = document.getElementById(id) ||
                 document.querySelector(`#${id} .optical-body`);
      if (el) ro.observe(el);
    });
  }

  // Also sync once on DOMContentLoaded for initial layout
  document.addEventListener('DOMContentLoaded', () => {
    canvasIds.forEach((id) => {
      const canvas = document.getElementById(id);
      if (canvas) syncCanvas(canvas);
    });
  });

  // And on window resize as a fallback
  window.addEventListener('resize', () => {
    canvasIds.forEach((id) => {
      const canvas = document.getElementById(id);
      if (canvas) syncCanvas(canvas);
    });
  });


  /* ── LOG UTILITY (stub for Stage 2) ──────────────────────────
     window.appendLogEntry is the single write path for all
     log entries. Stage 2 state.js calls this whenever a new
     event is dispatched. Defined here as a no-op stub so
     Stage 1 doesn't throw on any early calls.
  ─────────────────────────────────────────────────────────────── */
  window.appendLogEntry = window.appendLogEntry || function (entry) {
    // Stub. Real implementation in Stage 2 state.js.
    // entry: { ts: string, tag: string, msg: string, severity: 'nominal'|'warning'|'threat'|'info' }
    console.debug('[AOID] log (stub):', entry);
  };


  console.info('[AOID] ui.js Stage 1 loaded.');

})();
