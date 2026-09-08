# AOID — Autonomous Operations Intelligence Desk

## Run it
No build step, no dependencies. Two options:
1. **Double-click `index.html`** — opens directly in your browser.
2. **Or serve it** (avoids any local-file CORS quirks): `npx serve .` from this folder, then open the printed localhost URL.

By default the dashboard boots straight into **playback mode** — it plays `data/playback_log.json`, a 6-beat reconstruction that starts at the TCA (closest-approach) moment and unwinds backward through the decision, burn, and drag-uncertainty origin. Speed buttons (1×/2×/4×) are wired and working.

## File map
- `index.html` — page shell, all panels/IDs
- `style.css` — full instrument-panel visual system (tokens, layout, switch animation)
- `js/state.js` — canonical `AppState`, the single write/read path (`window.dispatch`, `window.subscribe`, `window.addEvent`)
- `js/canvas.js` — trajectory + optical feed canvas rendering
- `js/ingestion.js` — WebSocket listeners for live teammate nodes (ports 8001/8002/8003, currently placeholders)
- `js/playback.js` — the reversed-TCA demo playback engine (primary demo path)
- `js/mock.js` — dev-only static/ticking dataset, **not loaded by default** (see note in `index.html`) — only re-enable this for early layout dev before `playback_log.json` exists; don't run it alongside playback, they'll fight over `AppState`.
- `js/ui.js` — override switch, speed buttons, canvas resize handling

## Bringing in your teammates' real output
Two ways, pick based on what they hand you:

**A. A recorded run (recommended for the pitch)** — have them export their Basilisk/vision/drag results as a plain array of snapshot objects, one per timestep, matching the shape already in `data/playback_log.json` (see any entry in that file for the exact fields: `sat`, `debris`, `collision_prob`, `dca_km`, `ellipsoid`, `detections`, etc.). Drop it in as `data/playback_log.json` and playback.js picks it up automatically — zero code changes.

**B. Live sockets** — if a node is actually running and streaming, `js/ingestion.js` already listens on:
| Source | Port | Packet shape |
|---|---|---|
| Orbit/physics | `ws://localhost:8001` | `{type:"orbit", sat:{id,r,v}, debris:{id,r,v}, tca_s, dca_km, collision_prob, dv_vector, dv_mag_ms}` |
| Edge vision | `ws://localhost:8002` | `{type:"vision", frame_id, detections:[{cx,cy,w,h,conf}]}` |
| Drag ML | `ws://localhost:8003` | `{type:"drag", cd, ellipsoid:{along,cross,radial}, sigma}` |

If real ports/hosts differ, only `SOURCES` at the top of `js/ingestion.js` needs updating. `cx/cy/w/h` in vision detections are normalized 0–1 (fraction of frame width/height), not pixels.

For the actual judged pitch, favor option A — it has zero live-process/network dependency, which matters a lot more than "it's technically live" once you're standing in front of judges.

## Live wiring — now matched to the real code (not the placeholder schema)
Your teammates' `fsw_server.py`, `payload_manager.py`, and `ground_ai_node.py` already contain a `DashboardBroadcaster` on ports 8001/8002/8003, matching the requirements doc almost field-for-field. `ingestion.js`, `state.js`, and `canvas.js` are now updated to match their **actual** payloads (not the earlier placeholder):

- **Hosts**: `js/ingestion.js` top of file — `HOSTS` object has the three real IPs from `network_config.json`. If venue wifi changes them, edit those 3 lines only.
- **Orbit (8001)**: single position point per tick, no full arc — `ingestion.js` now approximates a circular orbit arc once from the first live radius so the trajectory canvas has lines to draw, same trick used for playback mode.
- **Vision (8002)**: no `detections[]` array is sent — the corridor HUD image arrives pre-rendered as a base64 JPEG (`hud_frame_path`). `canvas.js`'s processed-feed panel now decodes and displays that image directly instead of drawing synthetic bounding boxes. The raw-feed panel stays simulated since no raw video reaches the browser in this pipeline.
- **Decision**: real 3-way verdict (`EXECUTE_BURN` / `NO_MANEUVER_REQUIRED` / `MONITOR_PEER_MANEUVER`) + `GO`/`NO_GO` status, shown directly rather than inferred from scores client-side. Matrix labels relabeled `SELF`/`PEER` (asset names are dynamic, shown elsewhere).
- **TCA**: `ground_ai_node.py` sends an absolute UTC timestamp (`time_of_closest_approach`), not a live-ticking seconds value — `state.js` now runs a 500ms ticker that computes the countdown against wall clock.
- **Collision probability**: not computed anywhere in `ground_ai_node.py` — status strip shows `—` instead of a fake number.

**One open item**: the exact key names inside `scores.self` / `scores.peer` (from `decision_engine.py`) aren't confirmed — the matrix bars assume `fuel`/`lifetime`/`priority`/`downtime` (matching the original spec's 30/25/25/20 weights). If `decision_engine.py` uses different key names, the bars will silently show 0 for unmatched criteria rather than error — worth a quick live test to confirm before the pitch.

## Fixes applied in this pass
- Wrote the missing `index.html` (wasn't in the handoff) matching every ID/class the CSS and JS already expected — verified with a full cross-check, nothing missing.
- Disabled `mock.js` from the default load — it was set to run alongside `playback.js`, and both write to `AppState` on independent timers (tca_s, frame_id, detections), which would visibly flicker/fight during the demo.
- `data/playback_log.json` only ever carried single-point `sat.r`/`debris.r` per frame, never the full orbit arc canvas.js needs to draw the ellipse lines (`orbit_path_sat`/`orbit_path_debris`). Added a one-time orbit-arc generator to `playback.js` (same math `mock.js` used) so the trajectory panel actually renders the orbit lines now instead of sitting on "Awaiting orbit data…" the whole time.
- Verified: JS files are syntactically valid (`node --check`), `playback_log.json` parses and has 6 frames, HTML tag structure balances.

## Known gap — solo maneuver (spacecraft vs. debris) sends empty scores
Confirmed against `decision_engine.py`: `scoring_audit` has **two different shapes** depending on `conjunction_type`:
- `ACTIVE_ACTIVE_SPACECRAFT` → has `self`/`peer` keys (what `payload_manager.py`'s broadcast currently extracts)
- `SPACECRAFT_VS_DEBRIS` → uses `local_asset`/`local_score`/`local_factors` instead — **no `self`/`peer` keys at all**

Since `payload_manager.py`'s dashboard broadcast only ever reads `scoring_audit.get("self")` / `.get("peer")`, the debris-only case (your actual test scenario in `test_three_node_e2e.py`, `Debris_Obj_8492`) currently broadcasts **empty scores** to the dashboard. The UI now degrades gracefully for this (shows a solo-maneuver readout instead of a fake comparison), but if you want the real per-factor cost breakdown to show up for that case too, have your Edge teammate apply this small patch to `payload_manager.py` around the existing broadcast block:

```python
scores_data = getattr(decision_packet, "debug_scores", None) or getattr(decision_packet, "scoring_audit", None) or {}

if scores_data.get("conjunction_type") == "SPACECRAFT_VS_DEBRIS":
    self_score = {
        "asset_id": scores_data.get("local_asset"),
        "score":    scores_data.get("local_score"),
        "factors":  scores_data.get("local_factors", {}),
    }
    peer_score = None
else:
    self_score = scores_data.get("self")
    peer_score = scores_data.get("peer")

self.dashboard_ws.broadcast({
    "type": "vision",
    "ts": datetime.now(timezone.utc).timestamp(),
    "frame_id": 0,
    "corridor_intersected": is_hit,
    "hud_frame_path": b64_img,
    "decision": decision_packet.decision,
    "decision_status": "GO" if decision_packet.responsibility == "self" else "NO_GO",
    "target_asset": decision_packet.target_asset,
    "delta_v_vector_mps": decision_packet.delta_v_vector_mps,
    "delta_v_magnitude_mps": decision_packet.delta_v_magnitude_mps,
    "conjunction_type": scores_data.get("conjunction_type"),
    "weights": scores_data.get("weights", {}),
    "scores": {
        "self": self_score or {},
        "peer": peer_score or {}
    }
})
```
This just forwards `conjunction_type` and normalizes both shapes into the same `self`/`peer` wire format — the dashboard already knows how to render both (with or without a peer).

## Known gap — matrix criterion mapping & score direction
`decision_engine.py`'s cost factors are `cost_lifetime` / `cost_delta_v` / `cost_priority` / `cost_downtime` (nested under `factors`), and **lower cost wins** (more suitable to execute the burn) — opposite of a typical "higher bar wins" convention. `state.js` is now updated to match this exactly, using the server-precomputed `score` per side directly rather than recomputing client-side. Weight percentages shown in the matrix panel are now pulled live from each packet's `weights` object (`w_delta_v`/`w_lifetime`/`w_priority`/`w_downtime`) instead of the hardcoded 30/25/25/20 from the original spec — if `PayloadConfig`'s configured weights differ from that, the UI will now show the real numbers.
