# Product Requirements Document: Ground AI Node
## Project Kessler — Autonomous Operations Intelligence Desk (AOID)
**Module Owner:** Role 3 — Machine Learning Engineer (Ground AI)
**Document Version:** 1.0 — Hackathon Build
**Target Runtime:** 48-hour build window

---

## 1. Executive Summary

The Ground AI Node is the predictive front-end of the AOID pipeline. Its job is
narrow and specific: take in live space-weather telemetry and known ballistic
properties of the primary satellite and the debris object, and use that to
**tighten the uncertainty bound on a raw Conjunction Data Message (CDM)**
before it is acted on by the rest of the system.

Raw ground-radar CDMs carry large "error ellipsoids" because atmospheric drag
— which is highly sensitive to solar activity — is hard to model with static
coefficients. This node's entire value proposition is: *predict how much the
atmosphere will expand or contract in the near term, and shrink the
uncertainty ellipsoid accordingly.* A tighter ellipsoid means fewer
false-positive avoidance burns downstream.

This node does **not** do orbital propagation (that's Basilisk/Role 1) and
does **not** do optical detection (that's Role 2). It is a self-contained
service that:

1. Polls NOAA space-weather APIs on a fixed interval.
2. Runs a lightweight regression model to produce a drag multiplier and a
   covariance estimate.
3. Emits a strictly-typed "Refined CDM" JSON packet consumed by the Physics
   node and the Streamlit dashboard.

Because this is a hackathon build, the model itself is intentionally simple
and swappable — the scaffold is built so a trained XGBoost model (`.json` /
`.pkl`) can be dropped in without touching the ingestion or publishing code.

---

## 2. Data Flow Diagram (Text-Based)

```
                         ┌────────────────────────────┐
                         │   NOAA SWPC REST Endpoints  │
                         │  - 10cm-flux-30-day.json    │
                         │  - noaa-planetary-k-index   │
                         └──────────────┬───────────────┘
                                        │ poll (HTTP GET, every N sec)
                                        ▼
                         ┌────────────────────────────┐
                         │   GROUND AI NODE            │
                         │                              │
                         │  1. DataIngestor             │
                         │     - fetch F10.7            │
                         │     - fetch Kp index          │
                         │     - fallback cache          │
                         │                              │
                         │  2. FeatureBuilder            │
                         │     - [f107, kp, BC_sat,      │
                         │        BC_debris]             │
                         │                              │
                         │  3. DragPredictor (XGBoost)   │
                         │     - drag_multiplier         │
                         │     - covariance [σx,σy,σz]   │
                         │                              │
                         │  4. CDMBuilder                │
                         │     - merges raw conjunction  │
                         │       geometry + AI output    │
                         │       into Refined CDM        │
                         │                              │
                         │  5. Publisher                 │
                         │     - shared/cdm_packet.json  │
                         │     - TCP JSON broadcast       │
                         └──────────────┬───────────────┘
                                        │
                     ┌──────────────────┼───────────────────┐
                     ▼                                       ▼
        ┌─────────────────────────┐            ┌─────────────────────────┐
        │  Basilisk Physics Node   │            │  Streamlit Dashboard    │
        │  (consumes Refined CDM   │            │  (renders ellipsoid,    │
        │   to reduce integration  │            │   drag multiplier,      │
        │   uncertainty)           │            │   audit log)            │
        └─────────────────────────┘            └─────────────────────────┘
```

**Update cadence:** The node runs on a poll-predict-publish loop (default
every 60s for the hackathon demo; production would key this to NOAA's actual
update cadence, which is roughly every few minutes for Kp and daily for
F10.7).

---

## 3. Interfaces

### 3.1 Inputs

| Source | Field | Notes |
|---|---|---|
| NOAA SWPC `10cm-flux-30-day.json` | F10.7 solar flux | Most recent observation in series |
| NOAA SWPC `noaa-planetary-k-index.json` | Kp index | Most recent 3-hour estimate |
| Config / uplinked CDM | `BC_satellite` | Ballistic coefficient, primary asset |
| Config / uplinked CDM | `BC_debris` | Ballistic coefficient, secondary object |
| Config / uplinked CDM | Raw conjunction geometry | TCA, nominal miss distance, asset IDs |

### 3.2 Output — The Refined CDM Contract

This is the single artifact this node is responsible for producing. Every
field is required; the Physics and UI nodes should treat a missing field as a
parse error, not silently default it (fail loud during the hackathon so bugs
surface immediately).

```json
{
  "header": {
    "type": "REFINED_CDM",
    "timestamp_utc": "YYYY-MM-DDTHH:MM:SSZ"
  },
  "conjunction_data": {
    "primary_asset": "Kessler_Sat_1",
    "secondary_asset": "Debris_Obj_8492",
    "time_of_closest_approach": "YYYY-MM-DDTHH:MM:SSZ",
    "miss_distance_km": 1.2
  },
  "ai_drag_prediction": {
    "f107_flux": 185.4,
    "kp_index": 5.2,
    "drag_multiplier": 1.45,
    "ellipsoid_covariance_matrix": [150.0, 50.0, 50.0]
  },
  "action": "RECOMMEND_OPTICAL_CONFIRMATION"
}
```

Notes on fields:

- `drag_multiplier`: a scalar applied by the Physics node to its baseline
  drag coefficient. `1.0` = no adjustment; `>1.0` = atmosphere is predicted
  denser than nominal (higher drag, faster orbital decay, larger positional
  divergence); `<1.0` = thinner than nominal.
- `ellipsoid_covariance_matrix`: for the hackathon, represented as the
  diagonal of a 3×3 covariance matrix in the RIC (Radial/In-track/Cross-track)
  frame, in meters: `[σ_R, σ_I, σ_C]`. This is a simplification of a full 3×3
  matrix — defensible for a 48-hour build, and easy to upgrade to a full
  6-element upper-triangular form later if time allows.
- `action`: an enum. For v1, the only two values are
  `RECOMMEND_OPTICAL_CONFIRMATION` (miss distance uncertain enough that
  Edge Vision should attempt an optical fix) and `NO_ACTION` (confidence
  already high, skip the optical tasking to save power/time).

### 3.3 Transport

Two mechanisms, both implemented for redundancy given hackathon demo risk:

1. **File-based:** write atomically to `shared/cdm_packet.json` on every
   cycle. This is the simplest integration point for the Streamlit node and
   requires no socket handling on their end (`st.cache_data` + file mtime
   polling is enough).
2. **TCP broadcast:** a lightweight local TCP server that pushes the same
   JSON payload, newline-delimited, to any connected client (Basilisk node,
   or the Streamlit node if they'd rather subscribe than poll a file).

Both paths are driven from the same in-memory packet so they can never
disagree.

---

## 4. ML Model Architecture Justification

**Choice: XGBoost regressor (primary), with a simple LSTM path available as
fallback discussion, not default.**

Reasoning:

- **Feature count is tiny (4 inputs).** F10.7, Kp, BC_satellite, BC_debris.
  A gradient-boosted tree ensemble handles this kind of low-dimensional,
  tabular, non-linear relationship (drag response to solar flux is known to
  be non-linear) far better per training-minute than a deep network.
- **Training data will be small and synthetic/historical-TLE-derived.**
  XGBoost is far less prone to overfitting on the small, noisy datasets
  achievable in a 48-hour window than an LSTM or DNN would be.
- **Inference latency matters for the demo loop.** XGBoost inference is
  sub-millisecond on CPU; no GPU dependency, no framework weight (no
  TensorFlow/PyTorch install needed on a laptop mid-hackathon).
- **Interpretability for the judging demo.** Feature importances from
  XGBoost give an easy "here's why the model raised the drag multiplier"
  story for the presentation — directly visualizable on the dashboard.
- **LSTM is not ruled out**, and the scaffold below is designed so a
  time-series windowed model could be swapped in (feed it a rolling window
  of F10.7/Kp instead of a single point-in-time reading) if there's time
  left after the core pipeline is working. It is explicitly **not** the
  default path because it adds sequence-windowing complexity and training
  time the team doesn't have on day one.
- **Explicitly rejected:** deep CNNs/transformers. There is no image or
  sequence-of-images input to this node (that's the Edge Vision node's job),
  so there is no architectural justification for that additional complexity
  or training latency here.

**Target metric:** for the hackathon, correctness of *direction and rough
magnitude* of the drag multiplier matters more than tight RMSE — the judging
demo cares that the ellipsoid visibly tightens/loosens in response to solar
activity, not that the model is production-grade. Training on historical
TLE-derived decay rates vs. F10.7/Kp history (pulled from Space-Track.org) is
the intended ground truth if time allows; otherwise, a physically-motivated
heuristic (see `_mock_inference` in the scaffold) stands in as a placeholder
that already produces a directionally-correct signal for demo purposes.

---

## 5. Edge Cases & Failure Handling

| Scenario | Handling |
|---|---|
| NOAA API timeout / non-200 response | Fall back to last successfully cached value in memory; log a `stale_data: true` flag; if no cache exists yet, use conservative defaults (`f107=150.0`, `kp=3.0`, i.e. quiet-sun assumptions) so downstream nodes never crash on `None`. |
| NOAA API returns malformed / empty JSON | Same fallback path as timeout; do not propagate a parse exception up to the publish loop — one bad poll should never kill the service. |
| Model file not found / not yet trained | `DragPredictor` degrades to `_mock_inference()`, a deterministic, physically-motivated heuristic (drag scales with F10.7 and Kp) so the rest of the pipeline can be integrated and demoed *before* the real model is trained — this is the actual mechanism that unblocks Roles 1, 2, and 4 on Day 1. |
| Ballistic coefficients missing from config | Use published/typical BC defaults for a small LEO smallsat (`BC ≈ 100 kg/m²`) and for generic debris (`BC ≈ 30 kg/m²`), logged clearly as `bc_defaulted: true` so it's visible in the audit log. |
| Downstream consumer (Physics/UI) not yet listening on TCP | Publisher must not block or crash on a failed `send()` — wrap in try/except, drop the client, keep serving file-based output regardless. |
| Publish loop runs faster than NOAA data changes | Not a failure case — expected. The node still republishes on every cycle (cheap), since the conjunction geometry / TCA countdown is what's actually changing moment-to-moment for the UI, even if the drag inputs are static between NOAA refreshes. |
| Clock/timestamp drift between nodes | All timestamps emitted in UTC ISO-8601 (`Z` suffix) exclusively; no local time anywhere in the contract. |
| Two conjunctions active simultaneously (stretch case) | v1 handles one active conjunction context at a time (matches the Active-Active Avoidance Protocol's pairwise framing in the parent document); documented as a known v2 limitation, not solved in the 48-hour scope. |

---

## 6. Out of Scope (v1 / Hackathon)

- Full 3×3 (or 6×6) covariance matrix — diagonal approximation only.
- Model retraining pipeline / CI.
- Authentication or encryption on the TCP socket (local-loopback only,
  trusted-network hackathon assumption).
- Multi-conjunction concurrency.
