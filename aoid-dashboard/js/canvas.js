/**
 * canvas.js — Stage 2
 *
 * All canvas rendering: trajectory visualization + optical feeds.
 *
 * Architecture — two-layer separation:
 *   Layer 1 (background source): window.AOID.rawBackground / processedBackground
 *     — default: simulated star field
 *     — swap in Stage 3+: real frame / image data
 *   Layer 2 (overlay): fixed per-renderer — bboxes, markers, labels, vectors
 *
 * Render loop: requestAnimationFrame with dirty flag.
 * Only redraws after dispatch() updates AppState.
 *
 * Exposes:
 *   window.AOID.rawBackground        — swappable fn(ctx, w, h, state)
 *   window.AOID.processedBackground  — swappable fn(ctx, w, h, state)
 */

'use strict';

(function () {

  /* ── AOID namespace ─────────────────────────────────────── */
  window.AOID = window.AOID || {};


  /* ═══════════════════════════════════════════════════════════
     DESIGN TOKENS (mirror CSS custom properties)
  ═══════════════════════════════════════════════════════════ */
  const C = {
    bgBase:    '#0A0E14',
    bgOptical: '#070B10',
    border:    '#1C2530',
    dimText:   '#6B7684',
    primary:   '#C7D0D9',
    accent:    '#3E6B94',
    warning:   '#B9832E',
    nominal:   '#5C8A6B',
    threat:    '#A6473D',
    gridLine:  'rgba(28, 37, 48, 0.7)',
  };

  const FONT_MONO = '10px "JetBrains Mono", "Courier New", monospace';
  const FONT_UI   = '11px "Barlow Condensed", "Arial Narrow", Arial, sans-serif';


  /* ═══════════════════════════════════════════════════════════
     DIRTY FLAG + RAF LOOP
  ═══════════════════════════════════════════════════════════ */
  let _dirty = true;

  // Mark dirty whenever state changes
  window.subscribe(function () { _dirty = true; });

  function _startLoop() {
    function loop() {
      if (_dirty) {
        _dirty = false;
        _renderAll();
      }
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  function _renderAll() {
    const state = window.AppState;
    if (!state) return;
    _renderCanvas('canvas-trajectory', _drawTrajectory);
    _renderCanvas('canvas-raw',        _drawRawFeed);
    _renderCanvas('canvas-processed',  _drawProcessedFeed);
  }

  /* Syncs canvas pixel buffer to CSS layout size, then calls drawFn. */
  function _renderCanvas(id, drawFn) {
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const w = Math.floor(canvas.clientWidth);
    const h = Math.floor(canvas.clientHeight);
    if (w === 0 || h === 0) return;

    // Only reset buffer when dimensions actually change (avoids clearing on same size)
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;

    const ctx = canvas.getContext('2d');
    drawFn(ctx, w, h, window.AppState);
  }


  /* ═══════════════════════════════════════════════════════════
     ── TRAJECTORY / MANEUVER RENDERER ──────────────────────
  ═══════════════════════════════════════════════════════════ */

  function _drawTrajectory(ctx, w, h, state) {
    // 1. Background
    ctx.fillStyle = C.bgBase;
    ctx.fillRect(0, 0, w, h);

    // 2. Subtle dot-grid reference (cosmetic only)
    _drawDotGrid(ctx, w, h);

    // 3. Compute view transform from orbit paths
    const allPts = [
      ...(state.orbit_path_sat    || []),
      ...(state.orbit_path_debris || []),
    ];
    if (allPts.length < 2) {
      _drawNoData(ctx, w, h, 'Awaiting orbit data…');
      return;
    }

    const view = _computeView(allPts, w, h, 0.10);

    // 4. Orbit arcs
    _drawOrbitPath(ctx, state.orbit_path_sat,    view, C.accent,  1.5);
    _drawOrbitPath(ctx, state.orbit_path_debris, view, C.warning, 1.5);

    // 5. Earth origin marker (small crosshair at view center == origin)
    _drawOriginMarker(ctx, view);

    // 6. Error ellipsoid around debris
    if (state.ellipsoid && (state.ellipsoid.along > 0)) {
      _drawEllipsoid(ctx, state.debris, state.ellipsoid, view);
    }

    // 7. SAT-A → debris closest-approach line (thin, dotted)
    _drawConjunctionLine(ctx, state.sat, state.debris, view);

    // 8. Δv burn vector arrow at SAT-A
    if (state.dv_mag_ms > 0) {
      _drawDvVector(ctx, state.sat, state.dv_vector, state.dv_mag_ms, view, w, h);
    }

    // 9. Object markers
    _drawSatMarker(ctx,    state.sat,    view);
    _drawDebrisMarker(ctx, state.debris, view);
  }

  /* Dot-grid background reference */
  function _drawDotGrid(ctx, w, h) {
    const spacing = 48;
    ctx.fillStyle = C.gridLine;
    for (let x = spacing; x < w; x += spacing) {
      for (let y = spacing; y < h; y += spacing) {
        ctx.fillRect(x - 0.5, y - 0.5, 1, 1);
      }
    }
  }

  /* Compute a view that fits all orbit points with padding */
  function _computeView(points, w, h, pad) {
    let minX = Infinity, maxX = -Infinity;
    let minY = Infinity, maxY = -Infinity;
    for (const [px, py] of points) {
      if (px < minX) minX = px;
      if (px > maxX) maxX = px;
      if (py < minY) minY = py;
      if (py > maxY) maxY = py;
    }
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;
    const scaleX = (w * (1 - 2 * pad)) / rangeX;
    const scaleY = (h * (1 - 2 * pad)) / rangeY;
    const scale  = Math.min(scaleX, scaleY);
    const midX   = (minX + maxX) / 2;
    const midY   = (minY + maxY) / 2;
    const cx     = w / 2;
    const cy     = h / 2;

    return {
      scale,
      project([x, y, _z]) {
        return {
          x: cx + (x - midX) * scale,
          y: cy - (y - midY) * scale,   // flip Y (math→screen)
        };
      },
    };
  }

  /* Draw a complete orbit path as a polyline */
  function _drawOrbitPath(ctx, path, view, color, lineWidth) {
    if (!path || path.length < 2) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth   = lineWidth;
    ctx.globalAlpha = 0.55;
    ctx.beginPath();
    const start = view.project(path[0]);
    ctx.moveTo(start.x, start.y);
    for (let i = 1; i < path.length; i++) {
      const p = view.project(path[i]);
      ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  /* Tiny Earth crosshair at the coordinate origin (0,0) */
  function _drawOriginMarker(ctx, view) {
    const o = view.project([0, 0, 0]);
    const r = 6;
    ctx.save();
    ctx.strokeStyle = C.border;
    ctx.lineWidth   = 1;
    ctx.beginPath();
    ctx.arc(o.x, o.y, r, 0, Math.PI * 2);
    ctx.stroke();
    // cross hairs
    ctx.beginPath();
    ctx.moveTo(o.x - r - 3, o.y); ctx.lineTo(o.x + r + 3, o.y);
    ctx.moveTo(o.x, o.y - r - 3); ctx.lineTo(o.x, o.y + r + 3);
    ctx.stroke();
    ctx.restore();
  }

  /* Error ellipsoid — drawn exaggerated for visibility, dashed */
  function _drawEllipsoid(ctx, debris, ellipsoid, view) {
    const pos = view.project(debris.r);
    const s   = view.scale;

    // Compute screen-space velocity angle for ellipsoid orientation
    const vx  = debris.v[0];
    const vy  = debris.v[1];
    const angle = Math.atan2(-vy, vx); // negated Y for screen

    // Visual radii — clamped to sensible screen sizes
    // Physical km values are tiny at orbit scale; exaggerate × 500 for visibility
    const rAlong = Math.max(ellipsoid.along  * s * 600, 28);
    const rCross = Math.max(ellipsoid.cross  * s * 600, 10);

    ctx.save();
    ctx.translate(pos.x, pos.y);
    ctx.rotate(angle);

    ctx.beginPath();
    ctx.ellipse(0, 0, rAlong, rCross, 0, 0, Math.PI * 2);
    ctx.strokeStyle = C.nominal;
    ctx.lineWidth   = 1;
    ctx.setLineDash([3, 4]);
    ctx.globalAlpha = 0.65;
    ctx.stroke();
    // Faint fill
    ctx.fillStyle   = C.nominal;
    ctx.globalAlpha = 0.06;
    ctx.fill();
    ctx.restore();
  }

  /* Dotted line between satellite and debris (conjunction proximity) */
  function _drawConjunctionLine(ctx, sat, debris, view) {
    const a = view.project(sat.r);
    const b = view.project(debris.r);
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    if (dist < 3) return; // too close to draw
    ctx.save();
    ctx.strokeStyle = C.dimText;
    ctx.lineWidth   = 0.5;
    ctx.setLineDash([2, 5]);
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.restore();
  }

  /* Δv burn vector arrow from satellite position */
  function _drawDvVector(ctx, sat, dvVec, dvMag, view, canvasW, _canvasH) {
    const pos = view.project(sat.r);

    // Arrow length scaled to canvasW, clamped sensibly
    const len = Math.min(Math.max(canvasW * 0.14, 45), 95);

    const vx  = dvVec[0];
    const vy  = dvVec[1];
    const mag = Math.hypot(vx, vy) || 1;
    const angle = Math.atan2(-vy / mag, vx / mag); // screen-space

    ctx.save();
    ctx.translate(pos.x, pos.y);
    ctx.rotate(angle);

    // Shaft
    ctx.strokeStyle = '#FF4D4D';
    ctx.lineWidth   = 2.5;
    ctx.setLineDash([4, 2]);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(len, 0);
    ctx.stroke();

    // Arrowhead
    ctx.fillStyle = '#FF4D4D';
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(len, 0);
    ctx.lineTo(len - 10, -5);
    ctx.lineTo(len - 10,  5);
    ctx.closePath();
    ctx.fill();

    // Label "Δv"
    ctx.font        = FONT_MONO;
    ctx.fillStyle   = '#FF4D4D';
    ctx.fillText('Δv ' + dvMag.toFixed(2) + ' m/s', len + 6, 4);

    ctx.restore();
  }

  /* Satellite marker: filled square + high-contrast blue label */
  function _drawSatMarker(ctx, sat, view) {
    const p = view.project(sat.r);
    const r = 6;
    const color = '#4A9DFF'; // High-contrast electric blue

    ctx.save();
    // Inner square fill + sharp border
    ctx.fillStyle   = color;
    ctx.strokeStyle = C.bgBase;
    ctx.lineWidth   = 1.5;
    ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
    ctx.strokeRect(p.x - r, p.y - r, r * 2, r * 2);

    ctx.font      = FONT_MONO;
    ctx.fillStyle = C.accent;
    ctx.fillText(sat.id, p.x + r + 4, p.y + 4);
    ctx.restore();
  }

  /* Debris marker: rotated diamond + label */
  function _drawDebrisMarker(ctx, debris, view) {
    const p = view.project(debris.r);
    const r = 5;

    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(Math.PI / 4); // 45° = diamond
    ctx.fillStyle   = C.warning;
    ctx.strokeStyle = C.bgBase;
    ctx.lineWidth   = 1.5;
    ctx.fillRect(-r, -r, r * 2, r * 2);
    ctx.strokeRect(-r, -r, r * 2, r * 2);
    ctx.restore();

    ctx.font      = FONT_MONO;
    ctx.fillStyle = C.warning;
    ctx.fillText(debris.id, p.x + r + 4, p.y + 4);
  }

  /* No-data placeholder */
  function _drawNoData(ctx, w, h, msg) {
    ctx.fillStyle = C.dimText;
    ctx.font      = FONT_MONO;
    ctx.textAlign = 'center';
    ctx.fillText(msg, w / 2, h / 2);
    ctx.textAlign = 'left';
  }


  /* ═══════════════════════════════════════════════════════════
     ── STAR FIELD GENERATOR ─────────────────────────────────
     Deterministic LCG — same seed = same star field.
     Cached per canvas size; invalidated on resize.
  ═══════════════════════════════════════════════════════════ */
  const _sfCache = new Map();

  function _lcg(seed) {
    let s = seed >>> 0;
    return function () {
      s = Math.imul(s, 1664525) + 1013904223 | 0;
      return (s >>> 0) / 4294967295;
    };
  }

  function _getStarfield(w, h) {
    const key = `${w}:${h}`;
    if (_sfCache.has(key)) return _sfCache.get(key);

    const ofc = document.createElement('canvas');
    ofc.width  = w;
    ofc.height = h;
    _paintStarfield(ofc.getContext('2d'), w, h);
    _sfCache.set(key, ofc);
    return ofc;
  }

  function _paintStarfield(ctx, w, h) {
    // Very dark background
    ctx.fillStyle = C.bgOptical;
    ctx.fillRect(0, 0, w, h);

    const rng      = _lcg(0xC0FFEE);
    const numStars = Math.floor(w * h / 320);

    for (let i = 0; i < numStars; i++) {
      const sx   = rng() * w;
      const sy   = rng() * h;
      const size = 0.5 + rng() * 1.2;
      const bri  = 0.25 + rng() * 0.75;

      ctx.fillStyle = `rgba(200, 210, 220, ${bri.toFixed(2)})`;
      // Point stars — 1×1 or 2×2 pixels, never round
      ctx.fillRect(Math.floor(sx), Math.floor(sy), size > 1.2 ? 2 : 1, size > 1.2 ? 2 : 1);
    }

    // Add a few very faint extended stars (double-pixel "bright" stars)
    const rng2 = _lcg(0xDEAD);
    for (let i = 0; i < 6; i++) {
      const sx  = rng2() * w;
      const sy  = rng2() * h;
      const bri = 0.7 + rng2() * 0.3;
      ctx.fillStyle = `rgba(220, 225, 230, ${bri.toFixed(2)})`;
      ctx.fillRect(Math.floor(sx), Math.floor(sy), 2, 2);
    }

    // Pixel-level noise grain via ImageData
    const imgd = ctx.getImageData(0, 0, w, h);
    const d    = imgd.data;
    const rng3 = _lcg(0xFACE);
    for (let i = 0; i < d.length; i += 4) {
      const noise = (rng3() - 0.5) * 10;
      d[i]   = Math.max(0, Math.min(255, d[i]   + noise));
      d[i+1] = Math.max(0, Math.min(255, d[i+1] + noise));
      d[i+2] = Math.max(0, Math.min(255, d[i+2] + noise));
    }
    ctx.putImageData(imgd, 0, 0);
  }

  /*
   * Default background source functions — SWAPPABLE.
   * To use a real image/frame instead:
   *   window.AOID.rawBackground = function(ctx, w, h, state) {
   *     ctx.drawImage(myVideoFrame, 0, 0, w, h);
   *   };
   */
  window.AOID.rawBackground = function _defaultRawBg(ctx, w, h, _state) {
    const sf = _getStarfield(w, h);
    ctx.drawImage(sf, 0, 0);
  };

  window.AOID.processedBackground = function _defaultProcessedBg(ctx, w, h, _state) {
    // Same star field as raw feed (in real use, this may differ from raw)
    const sf = _getStarfield(w, h);
    ctx.drawImage(sf, 0, 0);
  };


  /* ═══════════════════════════════════════════════════════════
     ── OPTICAL FEED RENDERERS ───────────────────────────────
  ═══════════════════════════════════════════════════════════ */

  /* Raw feed: background only */
  function _drawRawFeed(ctx, w, h, state) {
    window.AOID.rawBackground(ctx, w, h, state);
    // Scan-line overlay (subtle CRT texture — one horizontal line per 4px)
    _drawScanLines(ctx, w, h, 0.04);
  }

  /* Processed feed: real HUD JPEG when available, else synthetic
     starfield + detections (mock/playback fallback). */
  function _drawProcessedFeed(ctx, w, h, state) {
    if (state.hud_image) {
      const img = _ensureHudImage(state.hud_image);
      if (img && img.complete && img.naturalWidth > 0) {
        _drawImageContain(ctx, img, w, h);
        return;
      }
      // Image requested but still decoding — dark background, no flash
      ctx.fillStyle = C.bgOptical;
      ctx.fillRect(0, 0, w, h);
      _drawNoData(ctx, w, h, 'Decoding HUD frame…');
      return;
    }

    window.AOID.processedBackground(ctx, w, h, state);
    _drawScanLines(ctx, w, h, 0.04);

    if (state.detections && state.detections.length > 0) {
      _drawDetections(ctx, w, h, state.detections);
    }
  }

  /* ── Real HUD image loader/cache ─────────────────────────
     edge_pro sends the corridor HUD as a full "data:image/jpeg;
     base64,..." URI per decision cycle (not a continuous stream).
     Cache by source string so we don't re-decode the same image
     every animation frame — only load when the URI actually changes. */
  let _hudImg = null;
  let _hudImgSrc = null;

  function _ensureHudImage(dataUri) {
    if (dataUri === _hudImgSrc && _hudImg) return _hudImg;
    _hudImgSrc = dataUri;
    const img = new Image();
    img.onload = () => { _dirty = true; };
    img.src = dataUri;
    _hudImg = img;
    return img;
  }

  /* Draw an image letterboxed to fit (object-fit: contain equivalent) */
  function _drawImageContain(ctx, img, w, h) {
    ctx.fillStyle = C.bgOptical;
    ctx.fillRect(0, 0, w, h);

    const scale = Math.min(w / img.naturalWidth, h / img.naturalHeight);
    const dw = img.naturalWidth  * scale;
    const dh = img.naturalHeight * scale;
    const dx = (w - dw) / 2;
    const dy = (h - dh) / 2;
    ctx.drawImage(img, dx, dy, dw, dh);
  }

  /* Subtle alternating-row tint (cosmetic) */
  function _drawScanLines(ctx, w, h, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle   = '#000000';
    for (let y = 0; y < h; y += 3) {
      ctx.fillRect(0, y, w, 1);
    }
    ctx.restore();
  }

  /*
   * Detection bounding boxes.
   * detections[i].cx / .cy are normalised [0..1] relative to feed canvas.
   * detections[i].w  / .h  are normalised [0..1] widths/heights.
   */
  function _drawDetections(ctx, w, h, detections) {
    const boxColor = '#FFB800'; // High contrast gold/amber
    for (const det of detections) {
      const px = det.cx * w;
      const py = det.cy * h;
      const pw = det.w  * w;
      const ph = det.h  * h;

      const x0 = px - pw / 2;
      const y0 = py - ph / 2;

      // Main bounding box
      ctx.strokeStyle = boxColor;
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([]);
      ctx.strokeRect(x0, y0, pw, ph);

      // Corner tick marks (military targeting style)
      const tk = Math.min(8, pw * 0.25, ph * 0.25);
      ctx.strokeStyle = boxColor;
      ctx.lineWidth   = 2;
      _drawCornerTicks(ctx, x0, y0, pw, ph, tk);

      // Confidence label badge background + text
      ctx.font      = '10px "JetBrains Mono", monospace';
      ctx.fillStyle = 'rgba(10, 14, 20, 0.85)';
      ctx.fillRect(x0, y0 - 13, 36, 12);
      ctx.fillStyle = boxColor;
      ctx.fillText(
        `DEB ${(det.conf * 100).toFixed(0)}%`,
        x0 + 2, y0 - 3
      );

      // Center crosshair
      ctx.strokeStyle = boxColor;
      ctx.lineWidth   = 1;
      ctx.globalAlpha = 0.8;
      ctx.beginPath();
      ctx.moveTo(px - 5, py); ctx.lineTo(px + 5, py);
      ctx.moveTo(px, py - 5); ctx.lineTo(px, py + 5);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  function _drawCornerTicks(ctx, x, y, w, h, tk) {
    ctx.beginPath();
    // TL
    ctx.moveTo(x, y + tk);       ctx.lineTo(x, y);       ctx.lineTo(x + tk, y);
    // TR
    ctx.moveTo(x + w - tk, y);   ctx.lineTo(x + w, y);   ctx.lineTo(x + w, y + tk);
    // BL
    ctx.moveTo(x, y + h - tk);   ctx.lineTo(x, y + h);   ctx.lineTo(x + tk, y + h);
    // BR
    ctx.moveTo(x + w - tk, y + h); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w, y + h - tk);
    ctx.stroke();
  }


  /* ═══════════════════════════════════════════════════════════
     BOOT
  ═══════════════════════════════════════════════════════════ */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _startLoop);
  } else {
    _startLoop();
  }

  console.info('[canvas] ready.');

})();
