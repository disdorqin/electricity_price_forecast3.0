# EFM3 V3.1-FINAL Technical Report

**Status**: FINAL_BLOCKED_EVIDENCE_PACKAGED
**Date**: 2026-07-16
**Research line**: FROZEN. No further model search.

## Summary

This report documents the complete EFM3 V3.1 research line, covering
CA-TRIDENT V4.1 through V3.1-FINAL. The final verdict:
**no safe candidate found, planned tracks blocked by infrastructure.**

## Research history
- V4.1: CA-TRIDENT — foundation
- V5, V5.1–V5.4: CA-FAILMODE — NegCorr, UnderCorr, fail-mode analysis
- V3.1: Full-history replay + 6 new candidate tracks (A–F)
- V3.1-R1: 8 correctness defects fixed
- V3.1-FINAL: Final convergence and packaging

## Key outcomes
- Production A05 (overall=21.689, maxDeg=17.47, P95=5.31) unchanged
- NegCorr w120/w180 retained as research components (not production)
- W270/W360/Expanding, Robust Direct Delta, Joint Midday — INFRA_BLOCKED
- No candidate passes production safety gates (maxDeg≤5, P95≤2)

## Bundle structure
See EFM3-Artifacts/final_packages/EFM3_V31_FINAL_PATCH_BUNDLE/

## For full details
See:
- docs/research/V31_RESEARCH_REPORT.md (V3.1-R1 corrected replay)
- docs/research/V31_FINAL_CONVERGENCE_REPORT.md (R2/R3 attempt)
- docs/research/BUSINESS_METRIC_CONTRACT.md (business metric spec)
- docs/research/DAILY_RISK_CONTRACT.md (daily risk spec)
- research_memory/CURRENT_STATE.yaml (full state)
