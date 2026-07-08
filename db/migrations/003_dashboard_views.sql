-- EFM3 Dashboard Views (migration 003)
--
-- Read-only MySQL views used by the backend read APIs and the frontend dashboard.
-- These never mutate data; they are convenience projections over the 15 base tables.
-- hour_business convention: 01:00->1 ... 23:00->23, 00:00->24.

-- Safe (re)create: MySQL has no CREATE OR REPLACE VIEW before 8.0.31, so DROP first.
SET @dev = '';

DROP VIEW IF EXISTS v_efm_latest_runs;
CREATE VIEW v_efm_latest_runs AS
SELECT run_id, target_date, chain_version, mode, status, delivery_status,
       exit_code, started_at, finished_at
FROM efm_runs
ORDER BY started_at DESC, target_date DESC
LIMIT 50;

DROP VIEW IF EXISTS v_efm_run_prediction_counts;
CREATE VIEW v_efm_run_prediction_counts AS
SELECT run_id,
       COUNT(*)                                   AS total_predictions,
       SUM(is_selected)                           AS selected_predictions,
       SUM(is_shadow)                             AS shadow_predictions
FROM efm_predictions
GROUP BY run_id;

DROP VIEW IF EXISTS v_efm_selected_predictions;
CREATE VIEW v_efm_selected_predictions AS
SELECT run_id, target_date, hour_business, stage, model_name,
       model_version, pred_price, selected_reason, is_shadow
FROM efm_predictions
WHERE is_selected = 1;

DROP VIEW IF EXISTS v_efm_shadow_safety;
CREATE VIEW v_efm_shadow_safety AS
SELECT
    (SELECT COUNT(*) FROM efm_predictions WHERE is_shadow = 1 AND is_selected = 1)
        AS shadow_selected_count,
    (SELECT COUNT(*) FROM efm_predictions
        WHERE is_selected = 1 AND stage IN ('selector_shadow','p3_shadow','extreme_price_shadow','shadow'))
        AS final_from_shadow_count,
    (SELECT COUNT(*) FROM efm_runs
        WHERE status = 'FAIL' OR delivery_status = 'FAILED_NO_DELIVERY')
        AS unsafe_run_count;

DROP VIEW IF EXISTS v_efm_dataset_readiness;
CREATE VIEW v_efm_dataset_readiness AS
SELECT dataset_id, target_date, status, row_counts, leakage_cutoff, canonical_hour_mapping
FROM efm_dataset_versions
ORDER BY target_date DESC;

DROP VIEW IF EXISTS v_efm_postflight_summary;
CREATE VIEW v_efm_postflight_summary AS
SELECT run_id,
       COUNT(*)              AS total_checks,
       SUM(passed)           AS passed_checks,
       SUM(NOT passed)       AS failed_checks
FROM efm_postflight_checks
GROUP BY run_id;

DROP VIEW IF EXISTS v_efm_delivery_summary;
CREATE VIEW v_efm_delivery_summary AS
SELECT run_id,
       COUNT(*) AS output_count,
       SUM(CASE WHEN output_type = 'submission_ready' THEN 1 ELSE 0 END) AS submission_outputs,
       SUM(CASE WHEN output_type = 'report' THEN 1 ELSE 0 END)          AS report_outputs
FROM efm_delivery_outputs
GROUP BY run_id;

DROP VIEW IF EXISTS v_efm_hourly_prediction_compare;
CREATE VIEW v_efm_hourly_prediction_compare AS
SELECT run_id, target_date, hour_business, model_name, stage,
       pred_price, is_selected, is_shadow
FROM efm_predictions
WHERE stage IN ('da_anchor','official_baseline','seasonal_da_router',
                'selector_shadow','p3_shadow','final_selected')
ORDER BY run_id, hour_business, stage;
