# P2.10 DA-SGDF Selector Handoff

## Ready

- Registry candidate: `configs/candidate_registry/realtime_da_sgdf_selector.yaml`
- Source experiment: `disdorqin/electricity_forecast_deep_sgdf_delta@0c0b59f`
- Best safe gate: conservative DA-primary selector
- Default model: DA anchor
- Auxiliary model: SGDFNet
- Candidate-level improvement: 19.23 vs DA 19.30

## Not ready

- No runtime adapter yet.
- Not shadow integrated.
- Not production.
- Not champion.
- No final output replacement.

## Total-chain boundary

A future 3.0 adapter must be default-off and must write only to a selector shadow directory, such as:

`outputs/runs/YYYY-MM-DD/realtime_da_sgdf_selector_shadow/`

It must not write:

- `final/`
- `submission_ready.csv`
- formal realtime champion registry
- delivery status

## Suggested next implementation

Build a P2.11 default-off shadow adapter:

- Read official 3.0 DA anchor and SGDFNet predictions.
- Apply conservative gate rules.
- Write selector prediction, chosen model, confidence, and reason.
- Preserve original realtime prediction.
- Compare against actual only when actual is available.
- Fail safely and fall back to no-op.

## Hard rules

- DA anchor remains the fallback.
- SGDFNet is auxiliary only.
- TimesFM remains experimental.
- RT916 and TimeMixer are not allowed in online critical path.
- P3 risk remains shadow/diagnostic unless separately promoted.
