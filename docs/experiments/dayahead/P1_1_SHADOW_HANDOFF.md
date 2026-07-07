# P1.1 Shadow Handoff to Total-Chain AI

## What Is Ready

| Item | Location | Status |
|------|----------|:------:|
| Shadow registry (4 candidates) | `configs/shadow_registry/dayahead_*.yaml` | ✅ Done |
| Shadow decision document | `docs/experiments/dayahead/P1_1_DAYAHEAD_SHADOW_DECISION.md` | ✅ Done |
| Gate fix summary | `docs/experiments/dayahead/P1_1_GATEFIX_SUMMARY.md` | ✅ Done |
| Feature frame lessons | `docs/experiments/dayahead/P1_1_FEATURE_FRAME_LESSONS.md` | ✅ Done |
| Shadow handoff document (this file) | `docs/experiments/dayahead/P1_1_SHADOW_HANDOFF.md` | ✅ Done |
| Registry contract tests | `tests/test_dayahead_shadow_registry.py` | ✅ Done |
| Fallback check script | `scripts/check_dayahead_shadow_registry.py` | ✅ Done |
| Experiment package (source) | `disdorqin/epf-sota-experiment` commit `3578229` | ✅ Done |
| Branch | `agent/p1.1-dayahead-shadow-integration` | ✅ Done |

## What Is NOT Ready (Intentionally)

| Item | Status | Reason |
|------|:------:|--------|
| Runtime shadow adapter | ❌ NOT done | Not in scope for P1.1. The rich feature builder lives in the experiment repo, not in 3.0 codebase. |
| `main.py` modification | ❌ NOT done | Shadow must not touch the main delivery pipeline. |
| `ledger_predict.py` modification | ❌ NOT done | Shadow predictions must not mix with live ledger predictions. |
| `final_outputs.py` modification | ❌ NOT done | Shadow outputs must not enter the delivery chain. |
| `submission_ready.csv` generation | ❌ NOT done | Shadow candidates must never write to `submission_ready.csv`. |
| 3.0 champion replacement | ❌ NOT done | Shadow is not champion. Champion stays as-is. |

**This is by design.** P1.1 delivers a **registry + documentation** integration only. Runtime execution is explicitly deferred.

---

## What the Total-Chain AI Must Do Later

If the total-chain reviewer decides to activate a shadow candidate for runtime observation:

1. **Port the rich feature builder**: Extract `build_features_rich()` from `epf-sota-experiment/scripts/run_dayahead_p1_walkforward.py` and build it as a reusable module in the 3.0 codebase (e.g., `pipelines/features_rich.py`). Do not duplicate the code — import from a shared location.

2. **Implement a shadow adapter**: Create `pipelines/shadow_dayahead_adapter.py` that:
   - Loads `configs/shadow_registry/dayahead_cfg05.yaml`
   - Runs the rich feature builder on D-1 data
   - Loads a pre-trained cfg05 LightGBM model
   - Produces predictions to a **shadow-only output directory** (e.g., `outputs/shadow/dayahead_cfg05/`)
   - Does **not** call any `ledger_predict`, `final_outputs`, or `submission_ready` function

3. **Compare shadow vs production outputs**: Write a comparison script that computes sMAPE separately for shadow and production, enabling side-by-side A/B analysis without any cross-contamination.

4. **NEVER let shadow touch production**: The shadow adapter must be explicitly gated by a flag (e.g., `--enable-shadow`) that defaults to `False`. It must not be part of the default pipeline.

---

## Known Risks

| Risk | Severity | Mitigation |
|------|:--------:|------------|
| Rich feature builder not in 3.0 codebase | High | Must port before any runtime test. Keep as standalone module, do not merge into existing files. |
| Model version mismatch | Medium | Shadow model was trained on 2024-06~2026-02. If production data changes substantially, retrain shadow from the experiment repo first. |
| CPU-only training slower than GPU | Low | 505s per 90d retrain is acceptable. For 180d window, budget ~31 min. |
| Period 17_24 slight regression (+0.07pp) | Low | Within noise. Still far better than faithful 2.5. |
| Shadow potentially distracts from champion | Medium | Document clearly that shadow must not replace champion. Shadow is for observation only. |

---

## Shadow Constraints (Non-Negotiable)

1. **Shadow NEVER writes `submission_ready.csv`**.
2. **Shadow NEVER replaces the production champion**.
3. **Shadow NEVER modifies `main.py`, `ledger_predict.py`, or `final_outputs.py`**.
4. **Shadow prediction outputs go to `outputs/shadow/` only**.
5. **Shadow adapter requires `--enable-shadow` flag. Default is OFF.**
6. **All shadow code lives in clearly marked files/functions** — e.g., `pipelines/shadow_*.py`, class names prefixed `Shadow*`.

These constraints are not negotiable. Any integration work that violates them must be rejected during review.

---

## How to Review

1. Read the shadow decision document first: `docs/experiments/dayahead/P1_1_DAYAHEAD_SHADOW_DECISION.md`
2. Verify registry YAMLs comply with the shadow contract: `python tests/test_dayahead_shadow_registry.py`
3. Read feature frame lessons for context: `docs/experiments/dayahead/P1_1_FEATURE_FRAME_LESSONS.md`
4. Read gate fix summary for methodology: `docs/experiments/dayahead/P1_1_GATEFIX_SUMMARY.md`
5. Decide: approve shadow registry, request changes, or reject.
