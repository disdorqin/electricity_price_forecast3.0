# EFM3 V3.1-FINAL — Acceptance Checklist

## Bundle completeness
- [ ] 00_READ_ME_FIRST.md exists — CHECK
- [ ] 01_FINAL_STATE.yaml exists and correct — CHECK
- [ ] 02_PACKAGE_MANIFEST.json exists and correct — CHECK
- [ ] MODEL_REGISTRY.yaml complete (all fields) — CHECK
- [ ] MODEL_CHANGELOG.yaml complete — CHECK
- [ ] MODEL_LINEAGE_GRAPH.md complete — CHECK
- [ ] INTEGRATION_ROLES.yaml complete — CHECK
- [ ] ARTIFACT_POINTERS.json complete — CHECK
- [ ] FILE_COPY_MAP.csv complete — CHECK
- [ ] ENTRYPOINTS.yaml complete — CHECK
- [ ] verify_bundle.py PASS — PENDING
- [ ] Final technical report present — PENDING
- [ ] DO_NOT_INTEGRATE.txt present — CHECK

## Security
- [ ] No absolute user paths — CHECK
- [ ] No secrets / .env / passwords — CHECK
- [ ] No raw market data — CHECK
- [ ] No database dumps — CHECK

## Production safety
- [ ] A05 not modified — CHECK
- [ ] promotion_allowed = false — CHECK
- [ ] Feature flags default off — CHECK
- [ ] UnderCorr default disabled — CHECK
- [ ] Deprecated models not registered for integration — CHECK
- [ ] Production patch not auto-applied — CHECK

## Handoff completeness
- [ ] integration_handoff/00_READ_BY_INTEGRATION_AI.md present — CHECK
- [ ] ENTRYPOINTS.yaml lists all integration points — CHECK
- [ ] FILE_COPY_MAP.csv maps all file locations — CHECK
- [ ] DO_NOT_INTEGRATE.txt lists rejected models — CHECK
