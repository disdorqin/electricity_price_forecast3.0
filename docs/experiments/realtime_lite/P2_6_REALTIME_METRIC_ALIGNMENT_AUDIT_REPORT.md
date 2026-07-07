# P2.6 Realtime Metric / Ledger Alignment Audit Report

## 1. Conflict Summary

| Month | P2.3 DA | P2.3 SGD | P2.5 DA | P2.5 SGD | Status |
|-------|-------:|--------:|-------:|--------:|--------|
| 2025-03 | 31.86 | **22.89** | **20.60** | 27.47 | CONFLICT |
| 2025-09 | 19.57 | **13.75** | **13.30** | 17.99 | CONFLICT |
| 2026-05 | 25.67 | **20.30** | **20.37** | 26.25 | CONFLICT |

P2.3 says SGDFNet always beats DA_anchor. P2.5 says DA_anchor always beats SGDFNet.
**Both cannot be true.**

## 2. Root Cause: One-Hour XLSX Midnight Shift Bug

**P2.5 used the xlsx fallback path** because the actual ledger was reset to the
Phase G winter version (32 days) by the P2.4 git checkout. The xlsx fallback
has a **one-hour index shift bug**:

- xlsx sorted by ds: [00:00, 01:00, 02:00, ..., 23:00]
- expected hour_business 1-24: [01:00, 02:00, ..., 23:00, 00:00]

Midnight (00:00) is at INDEX 0 but should be at INDEX 23 (hour_business=24).
The populated ledger correctly maps midnight→hb=24, but the xlsx fallback
does not apply this shift.

### Impact
- **P2.5 DA vs actual**: Both come from xlsx raw with same shift → **shift cancels**
  (DA comparison is directionally correct as a within-bug comparison)
- **P2.5 SGDFNet vs actual**: SGDFNet from LEDGER (correct ordering) vs
  actual from xlsx (shifted) → **shift does NOT cancel** → SGDFNet appears
  artificially worse than it is
- **P2.5 fusion analysis**: Mixes correctly-ordered SGDFNet predictions with
  shifted actuals → **all P2.5 fusion results are INVALID**

## 3. Field Mapping

| Item | P2.3 Source | P2.5 Source | Same? |
|------|-------------|-------------|-------|
| y_true (actual) | Populated ledger (correct hour mapping) ✅ | xlsx fallback (BUGGY: midnight @ idx 0) ❌ | NO |
| DA anchor | xlsx column, no shift fix ✅ | xlsx column, no shift fix ✅ | YES (both buggy, shifts cancel in same-source comp) |
| SGDFNet pred | Prediction ledger (sorted by hour_business) ✅ | Prediction ledger (sorted by hour_business) ✅ | YES |
| capped_smape | Identical implementation | Identical implementation | YES |

## 4. Corrected Values (xlsx with midnight shift fix)

Using xlsx with correct `[01:00, 02:00, ..., 23:00, 00:00]` ordering:

| Month | DA_anchor | SGDFNet | Winner | Notes |
|-------|---------:|--------:|--------|-------|
| 2025-03 | 20.60 | **22.89** | DA (barely) | P2.3 said SGDFNet by 9pts |
| 2025-09 | 13.30 | **13.75** | DA (barely) | P2.3 said SGDFNet by 5.8pts |
| 2026-05 | **20.37** | 20.30 | SGDFNet (barely) | P2.3 said SGDFNet by 5.4pts |

Note: These corrected values do NOT match P2.3's original values (DA=31.86)
either. P2.3 used the POPULATED LEDGER which may have deduplicated or
transformed data differently. The original populated ledger (8712 rows)
is no longer available — it was lost during git checkout.

## 5. Root Cause

**P2_6_ROOT_CAUSE: P2_5_ALIGNMENT_BUG**

## 6. Recommendation

**P2_6_RECOMMENDATION: FIX_P2_5_AND_RERUN**

## 7. Final Verdict

**P2_6_RESULT: FAIL**
