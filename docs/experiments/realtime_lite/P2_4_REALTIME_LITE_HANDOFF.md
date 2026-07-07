# P2.4 Realtime Lite Handoff

## Current State

The P2.4 branch (`agent/p2.4-realtime-lite-candidate-registry`) contains:

- **Candidate registry configs**: `configs/candidate_registry/realtime_sgdfnet_lite.yaml`
  and `realtime_timesfm_lite.yaml` — YAML declarations of the P2.3 candidates.
- **Decision documentation**: `docs/experiments/realtime_lite/` — 5 markdown files
  covering the full P2.2→P2.3 decision chain.
- **Registry validation tests**: `tests/test_realtime_lite_candidate_registry.py` —
  13 automated checks ensuring registry integrity.

## What is Ready

- ✅ SGDFNet candidate declaration (status: `candidate`)
- ✅ TimesFM candidate declaration (status: `experimental_result`)
- ✅ Decision rationale for all 4 models (sgdfnet/timemixer/rt916/timesfm)
- ✅ Slow model replacement plan
- ✅ TimesFM smoke test report
- ✅ Registry integrity tests passing
- ✅ All candidate rules verified (no submission_ready writes, no champion replacement)

## What is NOT Ready

- ❌ No runtime adapter — the candidate is registry-only
- ❌ No shadow comparison against 3.0 realtime champion
- ❌ no `main.py`, `ledger_predict.py`, or `final_outputs.py` modifications
- ❌ No production adapter review
- ❌ No SGDFNet + TimesFM ensemble integration
- ❌ TimesFM scheduling fix still pending

## What Total-Chain AI Must Do Later

1. **Implement realtime lite shadow adapter** — a standalone module that
   runs SGDFNet (and optionally TimesFM) alongside the 3.0 champion for
   A/B comparison.
2. **Compare against official 3.0 realtime output** — the shadow adapter
   must produce a non‑contaminating output that does not touch
   `submission_ready.csv` or `final_outputs`.
3. **Optionally add TimesFM to the lite ensemble** — after scheduler fix
   and wider smoke testing.
4. **Integrate P3 extreme price shadow/risk classifier** — as RT916
   replacement for spike detection.
5. **Production adapter review** — before any candidate graduates from
   `registry_only` to `shadow` or `champion`.

## Known Risks for Reviewer

- **Candidate status is `registry_only`** — not wired into any pipeline.
  Switching to `shadow` requires a dedicated adapter.
- **SGDFNet is a single-model realtime predictor** — not a fusion ensemble.
  It cannot replace the 2.5 four-model fusion claim.
- **TimesFM is `experimental_result`** — not `candidate`. Wider testing
  required.
- **P3 integration is not covered here** — separate workstream.
- **Candidate output must not pollute `submission_ready.csv`** — strictly
  registry + docs only in this branch.

## Boundaries

| File/dir | Status |
|----------|--------|
| `configs/candidate_registry/` | ✅ New, added |
| `docs/experiments/realtime_lite/` | ✅ New, added |
| `tests/test_realtime_lite_candidate_registry.py` | ✅ New, added |
| `main.py` | ❌ Not modified |
| `cli/parser.py` | ❌ Not modified |
| `pipelines/ledger_predict.py` | ❌ Not modified |
| `pipelines/final_outputs.py` | ❌ Not modified |
| `final_outputs.py` | ❌ Not modified |
| `submission_ready.csv` generation | ❌ Not touched |
| Data files (`data/`, `models/`, `outputs/`) | ❌ Not touched |
| `.env` | ❌ Not touched |
