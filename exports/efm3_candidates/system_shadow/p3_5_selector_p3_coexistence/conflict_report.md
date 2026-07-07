# Coexistence Conflict Report
## Overlap Analysis (20 test days / 480 hours)
- Selector SGDF hours: 14
- P3 corrected hours: 30
- Overlap hours (both applied): 0
- Conflict hours (sel pred != P3 corr): 0
- Conflict rate: 0.0% of overlap hours

## Assessment
Selector chooses between DA_anchor and SGDFNet Lite, outputting `output_pred`.
P3 applies shadow-only correction to the original fused prediction.
Since P3 is shadow-only (never replaces original_pred), there is NO operational conflict.
The corrected value is observational, not operational.
Selector and P3 operate on the same underlying fused prediction; P3 only adjusts for extreme cases.
Both outputs coexist independently in their respective shadow directories.

- Selector operates on original fused: original_pred
- P3 corrects extreme cases: shadow_corrected_pred (shadow-only)
- These are independent shadow diagnostics
- No runtime conflict
- No final contamination
- No exit_code impact
