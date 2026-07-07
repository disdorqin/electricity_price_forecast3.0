# Winter P3 Failure Cases

## Top Bad Days
- 2025-11-03 (status=ok): fp=15, fn_neg=0, caps=0, delta=-11.9048
- 2025-11-04 (status=ok): fp=13, fn_neg=0, caps=0, delta=-8.1321
- 2025-11-05 (status=ok): fp=8, fn_neg=0, caps=0, delta=-0.055
- 2025-12-05 (status=ok): fp=7, fn_neg=0, caps=0, delta=-5.8194
- 2025-11-11 (status=ok): fp=5, fn_neg=0, caps=0, delta=-0.7778
- 2025-11-15 (status=ok): fp=5, fn_neg=0, caps=0, delta=-0.5807
- 2025-11-18 (status=ok): fp=5, fn_neg=0, caps=0, delta=-2.4585
- 2025-11-21 (status=ok): fp=5, fn_neg=0, caps=0, delta=-0.5668
- 2025-11-22 (status=ok): fp=5, fn_neg=0, caps=0, delta=-1.9645
- 2025-11-24 (status=ok): fp=5, fn_neg=0, caps=0, delta=9.9074
- 2025-12-06 (status=ok): fp=5, fn_neg=0, caps=0, delta=-3.7378
- 2026-01-17 (status=ok): fp=5, fn_neg=0, caps=0, delta=-1.7947
- 2025-11-08 (status=ok): fp=4, fn_neg=0, caps=0, delta=-4.4695
- 2025-11-12 (status=ok): fp=4, fn_neg=0, caps=0, delta=-2.972
- 2025-11-19 (status=ok): fp=4, fn_neg=0, caps=0, delta=-1.5446
- 2025-11-29 (status=ok): fp=4, fn_neg=0, caps=0, delta=-3.5849
- 2026-01-29 (status=ok): fp=4, fn_neg=0, caps=0, delta=-2.8576
- 2025-11-06 (status=ok): fp=3, fn_neg=0, caps=0, delta=-3.4432
- 2025-11-28 (status=ok): fp=3, fn_neg=0, caps=0, delta=-0.6681
- 2025-11-30 (status=ok): fp=3, fn_neg=0, caps=0, delta=0.5924

## Root Cause
1. Negative classifier threshold (0.80) is conservative in early-month transitions
2. Spike classifier precision is inherently low (P3 P-phase showed P=0.118)
3. No risk pack available → degraded feature set
4. Acceptable for controlled shadow