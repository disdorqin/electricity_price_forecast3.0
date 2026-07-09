# EFM3 Backend Release Seal

> Release candidate seal for the backend delivery repository.
> Generated: 2026-07-09. Repo: `electricity_price_forecast3.0`.

## 1. Scope

| Item              | Value                                    |
| ----------------- | ---------------------------------------- |
| repo role         | backend / db / forecast ledger           |
| frontend included | NO                                       |
| DB                | MySQL                                    |
| API               | FastAPI                                  |
| latest merge      | PR #16                                   |
| latest merge SHA  | 1cb15b69951b423058c2df90714e51cf4d079d03 |

## 2. Verified Chain

| Check                | Result       |
| -------------------- | ------------ |
| DB init              | PASS         |
| PMOS CSV backfill    | PASS         |
| formal_sim Jan-Jun   | 181/181 PASS |
| postflight           | 8/8          |
| formal guard         | PASS         |
| API smoke            | 7/7          |
| metrics              | computed     |
| no submission export | PASS         |
| no password leak     | PASS         |

## 3. Metrics

Benchmark: day-ahead forecast (`final_selected`) vs real-time actual (`rt_actual`).

| Metric |  Value |
| ------ | -----: |
| SMAPE  | 49.70% |
| MAE    |  92.83 |
| RMSE   | 143.90 |
| WMAPE  | 30.92% |

Quarterly: Q1 SMAPE 37.58% (90d) · Q2 SMAPE 24.34% (91d) — Q2 better than Q1.

## 4. Known Limitations

| Limitation                         | Impact                        | Next                                   |
| ---------------------------------- | ----------------------------- | -------------------------------------- |
| data only verified Jan-Jun 2026    | full-year metrics incomplete  | backfill Jul-Dec when source available |
| 70 interpolated cells              | minor source-quality caveat   | show flags in reports/API              |
| baseline is DA anchor vs RT actual | benchmark, not final champion | future model comparison                |

## 5. Frontend Handoff

- OpenAPI ready: `docs/api/openapi.json`
- frontend should call backend only (no direct DB)
- no DB credentials in browser
- handoff guide: `docs/FRONTEND_HANDOFF.md`
- desensitized examples: `docs/api/FRONTEND_API_EXAMPLES.md`

## 6. Recommendation

BACKEND_RELEASE_RECOMMENDATION: READY_FOR_FRONTEND_INTEGRATION

## 7. Final Verdict

BACKEND_RELEASE_RESULT: PASS
