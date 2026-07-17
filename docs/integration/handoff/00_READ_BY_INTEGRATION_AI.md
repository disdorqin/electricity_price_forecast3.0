# EFM3 V3.1-FINAL — Integration Handoff

**Read this first if you are the Integration AI.**

## 1. Current production A05 modified?
**NO.** Production is UNCHANGED. A05 = 0.5*DD + 0.5*IHMAE. Do NOT modify.

## 2. Research-only files
All files in `tools/research/`, `tests/research/`, `docs/research/`, `research_memory/`
are research-only. They are NOT imported by the production main chain.

## 3. Modified existing components
- **NegCorr** (`correction_modules/negcorr/`): Correction module only. 
  Do NOT deploy as primary. `default_enabled: false`.

## 4. New backup candidates
**NONE.** No new candidate passed screening. 

## 5. Correction modules
- `negcorr/` — negMAE improvement (48.3 vs 62.6). Shadow only.
- `undercorr/` — Research reference. Cold-start unsafe. Not for deployment.
- `failmode_reference/` — V5 Ca-Failmode architecture reference. Not production.

## 6. Do NOT integrate
- PC1 curve correction
- UnderCorr (cold-start unsafe)
- Original Trident
- Central / Spike experts
- Any `DEPRECATED` model in `model_registry/MODEL_REGISTRY.yaml`

## 7. Shadow priority
If any candidate is to be shadowed: **NegCorr_w120** (best negMAE/negSA).

## 8. Fallback
Production A05 is the default fallback. DD is unconditional fallback.

## 9. Feature flag
All research candidates have `default_enabled: false`.
Proposed production flag name: `EFM3_ENABLE_NEGCORR` (default: false).

## 10. Artifact loading
NegCorr models: `EFM3-Artifacts/ca_failmode_v5/negcorr_w120_w180.pkl`.

## 11. Input fields
- business_day, hour_business
- rt_actual (past only), da_actual (past only)
- legal_oos_da_pred / da_oos_pred
- exogenous forecasts (load, solar, wind, net_load, etc.)
- calendar features

## 12. Output fields
- 24-hour RT price prediction (business_day aligned, hour_business 1..24)
- Optional: uncertainty estimate, fallback_reason

## 13. Fallback conditions
- Missing exogenous forecast → use lagged actuals → if still missing → return DD
- Model training failure → return A05
- Prediction contains NaN → return DD with fallback_reason="nan"

## 14. Monitoring metrics
- smape_floor50 (pooled, daily)
- maxDeg (full 24h daily degradation vs DD)
- P95 (daily degradation)
- negMAE
- negSA

## 15. Rollback
Revert `EFM3_ENABLE_NEGCORR` flag to false. Restart circuit.

## Integration order
Step 1: Load contracts and tests only (verify understanding).
Step 2: Shadow NegCorr (write shadow prediction log, do NOT change final output).
Step 3: Verify 24h prediction shape and hash.
Step 4: Verify business metric parity (smape_floor50 vs V5 panel).
Step 5: **Maintainer approval required** before enabling any candidate.
