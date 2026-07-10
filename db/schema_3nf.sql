-- ============================================================================
-- EFM3 3.0 Production Ledger — 3NF Schema (normalized)
-- ============================================================================
-- Design goals (third normal form):
--   * No redundant `target_date` in run-children: every fact table references
--     `efm_runs(run_id)` and derives target_date via JOIN (removes the
--     transitive dependency run_id -> target_date -> row that existed before).
--   * Every open/evolving free-text domain (stage, model, policy, check,
--     output_type, artifact_type, event_type, data_type, relation, rule,
--     repair_stage, market, unit, source_type, import_status, step_name)
--     is a FOREIGN KEY to a `efm_dim_*` lookup table. Names are resolved to
--     surrogate ids at the data-access boundary.
--   * Closed, static domains (task, status, severity, metric_scope, mode) stay
--     as ENUM — an atomic, constrained domain is itself 3NF-compliant and
--     avoids an explosion of lookup tables for values that never change.
--   * `efm_actual_prices` / `efm_market_data_hourly` / `efm_dataset_versions`
--     keep their business-date columns because those ARE the natural key
--     (not a transitive dependency on run_id).
--
-- MySQL is the source of truth; local CSVs (outputs/<date>/) are derived
-- artifacts produced by the pipeline exporter.
-- ============================================================================

SET FOREIGN_KEY_CHECKS = 0;
SET NAMES utf8mb4;

-- ── Dimension / lookup tables (created first; no cross-deps) ──────────────

CREATE TABLE IF NOT EXISTS efm_dim_stage (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_model (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_policy (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_check (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_output (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_artifact (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_event (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_datatype (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_relation (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_rule (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_repairstage (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_market (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_unit (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_sourcetype (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_importstatus (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS efm_dim_step (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    description VARCHAR(255) DEFAULT NULL,
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 1. efm_runs (run registry; target_date is a full dep on run_id = OK) ──

CREATE TABLE IF NOT EXISTS efm_runs (
    run_id          VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date     DATE            NOT NULL,
    chain_version   VARCHAR(32)     NOT NULL DEFAULT '3.0-db-ledger-3nf',
    mode            ENUM('dry_run','shadow','formal','formal_sim') NOT NULL DEFAULT 'dry_run',
    git_sha         VARCHAR(40)     DEFAULT NULL,
    config_hash     VARCHAR(64)     DEFAULT NULL,
    status          ENUM('PENDING','RUNNING','COMPLETE','PARTIAL','FAIL','CANCELLED','NEEDS_MODEL_OUTPUT') NOT NULL DEFAULT 'PENDING',
    delivery_status ENUM('NORMAL','DEGRADED_DELIVERED','FAILED_NO_DELIVERY','NOT_ATTEMPTED') NOT NULL DEFAULT 'NOT_ATTEMPTED',
    exit_code       INT             NOT NULL DEFAULT 0,
    started_at      DATETIME(3)     DEFAULT NULL,
    finished_at     DATETIME(3)     DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_target_date (target_date),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 2. efm_actual_prices (dimension; business key = date+hour, NOT a run-child) ──

CREATE TABLE IF NOT EXISTS efm_actual_prices (
    id          BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    target_date DATE            NOT NULL,
    hour_business TINYINT       NOT NULL,
    period      VARCHAR(8)      GENERATED ALWAYS AS (
                    CASE WHEN hour_business BETWEEN 1 AND 8 THEN '1_8'
                         WHEN hour_business BETWEEN 9 AND 16 THEN '9_16'
                         ELSE '17_24' END
                ) STORED,
    da_anchor   DECIMAL(12,4)   DEFAULT NULL,
    rt_actual   DECIMAL(12,4)   DEFAULT NULL,
    source_file VARCHAR(255)    DEFAULT NULL,
    loaded_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_date_hour (target_date, hour_business),
    INDEX idx_target_date (target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 3. efm_feature_snapshots (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_feature_snapshots (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    hour_business   TINYINT         NOT NULL,
    feature_json    JSON            DEFAULT NULL,
    feature_hash    VARCHAR(64)     DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run_target (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 4. efm_predictions (the core fact; stage/model are FKs to dims) ──

CREATE TABLE IF NOT EXISTS efm_predictions (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    hour_business   TINYINT         NOT NULL,
    task            ENUM('dayahead','realtime','fusion','final','shadow','delivery') NOT NULL,
    stage_id        INT UNSIGNED    NOT NULL,
    model_id        INT UNSIGNED    NOT NULL,
    model_version   VARCHAR(32)     DEFAULT 'unknown',
    pred_price      DECIMAL(12,4)   NOT NULL,
    is_shadow       BOOLEAN         NOT NULL DEFAULT FALSE,
    is_selected     BOOLEAN         NOT NULL DEFAULT FALSE,
    selected_reason VARCHAR(255)    DEFAULT NULL,
    cutoff_time     DATETIME(3)     DEFAULT NULL,
    quality_flags   JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)    REFERENCES efm_runs(run_id)      ON DELETE CASCADE,
    FOREIGN KEY (stage_id)   REFERENCES efm_dim_stage(id),
    FOREIGN KEY (model_id)   REFERENCES efm_dim_model(id),
    UNIQUE KEY uk_run_hour_stage_model (run_id, hour_business, stage_id, model_id),
    INDEX idx_run_id (run_id),
    INDEX idx_run_selected (run_id, is_selected, task),
    INDEX idx_run_stage (run_id, stage_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 5. efm_fusion_decisions ──

CREATE TABLE IF NOT EXISTS efm_fusion_decisions (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    hour_business           TINYINT         NOT NULL,
    policy_id               INT UNSIGNED    NOT NULL,
    base_model_id           INT UNSIGNED    DEFAULT NULL,
    selected_model_id       INT UNSIGNED    NOT NULL,
    selected_prediction_id  BIGINT          DEFAULT NULL,
    decision_reason         VARCHAR(255)    DEFAULT NULL,
    decision_json           JSON            DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)               REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (policy_id)             REFERENCES efm_dim_policy(id),
    FOREIGN KEY (base_model_id)         REFERENCES efm_dim_model(id),
    FOREIGN KEY (selected_model_id)     REFERENCES efm_dim_model(id),
    FOREIGN KEY (selected_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    UNIQUE KEY uk_run_hour_policy (run_id, hour_business, policy_id),
    INDEX idx_run_target (run_id),
    INDEX idx_sel_pred (selected_prediction_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 6. efm_postflight_checks ──

CREATE TABLE IF NOT EXISTS efm_postflight_checks (
    id          BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id      VARCHAR(64)     NOT NULL,
    check_id    INT UNSIGNED    NOT NULL,
    passed      BOOLEAN         NOT NULL,
    details     TEXT            DEFAULT NULL,
    checked_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)   REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (check_id) REFERENCES efm_dim_check(id),
    INDEX idx_run_check (run_id, check_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 7. efm_delivery_outputs ──

CREATE TABLE IF NOT EXISTS efm_delivery_outputs (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    output_type_id  INT UNSIGNED    NOT NULL,
    output_path     VARCHAR(512)    NOT NULL,
    file_hash       VARCHAR(64)     DEFAULT NULL,
    row_count       INT             DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)          REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (output_type_id)   REFERENCES efm_dim_output(id),
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 8. efm_model_registry (catalog; kept as-is) ──

CREATE TABLE IF NOT EXISTS efm_model_registry (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    model_name      VARCHAR(64)     NOT NULL,
    model_version   VARCHAR(32)     NOT NULL,
    task            ENUM('dayahead','realtime','fusion','shadow') NOT NULL,
    status          ENUM('active','shadow','deprecated','archived') NOT NULL DEFAULT 'shadow',
    description     TEXT            DEFAULT NULL,
    registered_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_name_version (model_name, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 9. efm_run_events ──

CREATE TABLE IF NOT EXISTS efm_run_events (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    event_type_id   INT UNSIGNED    NOT NULL,
    event_name      VARCHAR(128)    NOT NULL,
    event_detail    TEXT            DEFAULT NULL,
    event_json      JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)        REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (event_type_id) REFERENCES efm_dim_event(id),
    INDEX idx_run (run_id),
    INDEX idx_type (run_id, event_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 10. efm_artifacts ──

CREATE TABLE IF NOT EXISTS efm_artifacts (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    artifact_type_id INT UNSIGNED   NOT NULL,
    file_path       VARCHAR(512)    NOT NULL,
    file_size_bytes BIGINT          DEFAULT NULL,
    file_hash       VARCHAR(64)     DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)           REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (artifact_type_id)  REFERENCES efm_dim_artifact(id),
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 11. efm_data_sources ──

CREATE TABLE IF NOT EXISTS efm_data_sources (
    source_id       VARCHAR(64)     NOT NULL PRIMARY KEY,
    source_name     VARCHAR(128)    NOT NULL,
    source_type_id  INT UNSIGNED    NOT NULL,
    market_id       INT UNSIGNED    NOT NULL,
    root_path       VARCHAR(512)    DEFAULT NULL,
    path_pattern    VARCHAR(255)    DEFAULT NULL,
    enabled         BOOLEAN         NOT NULL DEFAULT TRUE,
    config_json     JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    FOREIGN KEY (source_type_id) REFERENCES efm_dim_sourcetype(id),
    FOREIGN KEY (market_id)       REFERENCES efm_dim_market(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 12. efm_source_files ──

CREATE TABLE IF NOT EXISTS efm_source_files (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_id       VARCHAR(64)     NOT NULL,
    file_path       VARCHAR(1024)   NOT NULL,
    file_name       VARCHAR(255)    NOT NULL,
    file_ext        VARCHAR(16)     NOT NULL,
    file_size       BIGINT          DEFAULT NULL,
    file_mtime      DATETIME(3)     DEFAULT NULL,
    file_sha256     VARCHAR(64)     DEFAULT NULL,
    detected_at     DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    imported_at     DATETIME(3)     DEFAULT NULL,
    import_status_id INT UNSIGNED   NOT NULL,
    import_message  TEXT            DEFAULT NULL,
    metadata_json   JSON            DEFAULT NULL,
    FOREIGN KEY (source_id)        REFERENCES efm_data_sources(source_id) ON DELETE CASCADE,
    FOREIGN KEY (import_status_id) REFERENCES efm_dim_importstatus(id),
    UNIQUE KEY uk_source_file (source_id, file_sha256(64), file_name(128)),
    INDEX idx_source (source_id),
    INDEX idx_status (import_status_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 13. efm_data_update_runs ──

CREATE TABLE IF NOT EXISTS efm_data_update_runs (
    update_run_id   VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date     DATE            DEFAULT NULL,
    source_root     VARCHAR(512)    DEFAULT NULL,
    mode            ENUM('scan_only','incremental','full_refresh') NOT NULL DEFAULT 'incremental',
    status          ENUM('PENDING','SCANNING','IMPORTING','COMPLETE','PARTIAL','FAIL') NOT NULL DEFAULT 'PENDING',
    files_detected  INT             NOT NULL DEFAULT 0,
    files_imported  INT             NOT NULL DEFAULT 0,
    rows_imported   INT             NOT NULL DEFAULT 0,
    started_at      DATETIME(3)     DEFAULT NULL,
    finished_at     DATETIME(3)     DEFAULT NULL,
    message         TEXT            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 14. efm_market_data_hourly ──

CREATE TABLE IF NOT EXISTS efm_market_data_hourly (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    market_id       INT UNSIGNED    NOT NULL,
    data_type_id    INT UNSIGNED    NOT NULL,
    trade_date      DATE            NOT NULL,
    hour_business   TINYINT         NOT NULL,
    value           DECIMAL(16,4)   DEFAULT NULL,
    unit_id         INT UNSIGNED    DEFAULT NULL,
    source_file_id  BIGINT          DEFAULT NULL,
    quality_flags   JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (market_id)      REFERENCES efm_dim_market(id),
    FOREIGN KEY (data_type_id)   REFERENCES efm_dim_datatype(id),
    FOREIGN KEY (unit_id)        REFERENCES efm_dim_unit(id),
    FOREIGN KEY (source_file_id) REFERENCES efm_source_files(id) ON DELETE SET NULL,
    UNIQUE KEY uk_market_type_date_hour (market_id, data_type_id, trade_date, hour_business),
    INDEX idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 15. efm_dataset_versions ──

CREATE TABLE IF NOT EXISTS efm_dataset_versions (
    dataset_id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date             DATE            NOT NULL,
    market_id               INT UNSIGNED    NOT NULL,
    source_file_hashes      JSON            DEFAULT NULL,
    row_counts              JSON            DEFAULT NULL,
    canonical_hour_mapping  BOOLEAN         NOT NULL DEFAULT TRUE,
    leakage_cutoff          DATETIME(3)     DEFAULT NULL,
    status                  ENUM('READY','PARTIAL','FAIL','BUILDING') NOT NULL DEFAULT 'BUILDING',
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (market_id) REFERENCES efm_dim_market(id),
    INDEX idx_target_date (target_date),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 16. efm_prediction_batches (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_prediction_batches (
    batch_id            VARCHAR(96)     NOT NULL,
    run_id              VARCHAR(64)     NOT NULL,
    task                ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    stage_id            INT UNSIGNED    NOT NULL,
    model_id            INT UNSIGNED    DEFAULT NULL,
    model_version       VARCHAR(32)     DEFAULT NULL,
    source_step_id      INT UNSIGNED    DEFAULT NULL,
    row_count           INT             NOT NULL DEFAULT 0,
    is_final_candidate  BOOLEAN         NOT NULL DEFAULT FALSE,
    is_shadow           BOOLEAN         NOT NULL DEFAULT FALSE,
    artifact_id         VARCHAR(96)     DEFAULT NULL,
    batch_hash          VARCHAR(64)     DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    metadata_json       JSON            DEFAULT NULL,
    PRIMARY KEY (batch_id),
    FOREIGN KEY (run_id)         REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id)        REFERENCES efm_dim_stage(id),
    FOREIGN KEY (model_id)        REFERENCES efm_dim_model(id),
    FOREIGN KEY (source_step_id)  REFERENCES efm_dim_step(id),
    INDEX idx_run_stage (run_id, task, stage_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 17. efm_prediction_lineage_edges (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_prediction_lineage_edges (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    parent_prediction_id    BIGINT          DEFAULT NULL,
    child_prediction_id     BIGINT          DEFAULT NULL,
    relation_id             INT UNSIGNED    NOT NULL,
    relation_json           JSON            DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)               REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (relation_id)          REFERENCES efm_dim_relation(id),
    FOREIGN KEY (parent_prediction_id)  REFERENCES efm_predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (child_prediction_id)   REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_parent (parent_prediction_id),
    INDEX idx_child (child_prediction_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 18. efm_pipeline_steps (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_pipeline_steps (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    task            ENUM('dayahead','realtime','fusion','delivery') NOT NULL DEFAULT 'dayahead',
    step_name_id    INT UNSIGNED    NOT NULL,
    step_order     SMALLINT        NOT NULL,
    status          ENUM('PENDING','RUNNING','COMPLETE','PARTIAL','FAIL','SKIPPED','NEEDS_MODEL_OUTPUT') NOT NULL DEFAULT 'PENDING',
    input_count     INT             DEFAULT NULL,
    output_count    INT             DEFAULT NULL,
    started_at      DATETIME(3)     DEFAULT NULL,
    finished_at     DATETIME(3)     DEFAULT NULL,
    runtime_ms      INT             DEFAULT NULL,
    message         VARCHAR(512)    DEFAULT NULL,
    config_json     JSON            DEFAULT NULL,
    metrics_json    JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)        REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (step_name_id)   REFERENCES efm_dim_step(id),
    INDEX idx_run_step (run_id, step_order),
    INDEX idx_step_name (run_id, step_name_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 19. efm_repair_decisions (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_repair_decisions (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    task                    ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    hour_business           TINYINT         NOT NULL,
    repair_stage_id         INT UNSIGNED    NOT NULL,
    source_prediction_id    BIGINT          DEFAULT NULL,
    repaired_prediction_id  BIGINT          DEFAULT NULL,
    rule_id                 INT UNSIGNED    NOT NULL,
    before_value            DECIMAL(12,4)   DEFAULT NULL,
    after_value             DECIMAL(12,4)   DEFAULT NULL,
    reason                  VARCHAR(255)    DEFAULT NULL,
    severity                ENUM('info','warning','critical') NOT NULL DEFAULT 'info',
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)                REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (repair_stage_id)        REFERENCES efm_dim_repairstage(id),
    FOREIGN KEY (rule_id)                REFERENCES efm_dim_rule(id),
    FOREIGN KEY (source_prediction_id)   REFERENCES efm_predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (repaired_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_run_hour (run_id, hour_business)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 20. efm_fusion_candidates (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_fusion_candidates (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    task                    ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    hour_business           TINYINT         NOT NULL,
    candidate_prediction_id BIGINT          DEFAULT NULL,
    candidate_model_id      INT UNSIGNED    NOT NULL,
    candidate_stage_id      INT UNSIGNED    NOT NULL,
    weight_value            DECIMAL(10,6)   DEFAULT NULL,
    rank_value              INT             DEFAULT NULL,
    score_json              JSON            DEFAULT NULL,
    selected                BOOLEAN         NOT NULL DEFAULT FALSE,
    rejected_reason         VARCHAR(255)    DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)                 REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_model_id)     REFERENCES efm_dim_model(id),
    FOREIGN KEY (candidate_stage_id)     REFERENCES efm_dim_stage(id),
    FOREIGN KEY (candidate_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_run_hour (run_id, hour_business),
    INDEX idx_selected (run_id, selected)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 21. efm_task_finals (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_task_finals (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    task                    ENUM('dayahead','realtime') NOT NULL,
    hour_business           TINYINT         NOT NULL,
    final_prediction_id     BIGINT          DEFAULT NULL,
    final_stage_id          INT UNSIGNED    NOT NULL,
    final_price             DECIMAL(12,4)   NOT NULL,
    source_policy_id        INT UNSIGNED    DEFAULT NULL,
    confidence_score        DECIMAL(6,4)    DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)             REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (final_stage_id)      REFERENCES efm_dim_stage(id),
    FOREIGN KEY (source_policy_id)    REFERENCES efm_dim_policy(id),
    FOREIGN KEY (final_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    UNIQUE KEY uk_run_task_hour (run_id, task, hour_business),
    INDEX idx_task (run_id, task)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 22. efm_delivery_finals (run-child; no target_date) ──

CREATE TABLE IF NOT EXISTS efm_delivery_finals (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    hour_business           TINYINT         NOT NULL,
    dayahead_final_id       BIGINT          DEFAULT NULL,
    realtime_final_id       BIGINT          DEFAULT NULL,
    delivery_prediction_id  BIGINT          DEFAULT NULL,
    delivery_price          DECIMAL(12,4)   NOT NULL,
    delivery_policy_id      INT UNSIGNED    NOT NULL,
    separator_rule_id       INT UNSIGNED    DEFAULT NULL,
    fallback_reason         VARCHAR(255)    DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id)                 REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (dayahead_final_id)      REFERENCES efm_task_finals(id) ON DELETE SET NULL,
    FOREIGN KEY (realtime_final_id)      REFERENCES efm_task_finals(id) ON DELETE SET NULL,
    FOREIGN KEY (delivery_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (delivery_policy_id)     REFERENCES efm_dim_policy(id),
    FOREIGN KEY (separator_rule_id)      REFERENCES efm_dim_rule(id),
    UNIQUE KEY uk_run_hour (run_id, hour_business),
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 23. efm_metric_runs (metric window; target_date_start/end are attributes) ──

CREATE TABLE IF NOT EXISTS efm_metric_runs (
    metric_run_id   VARCHAR(96)     NOT NULL,
    run_id          VARCHAR(64)     DEFAULT NULL,
    target_date_start DATE           NOT NULL,
    target_date_end   DATE           NOT NULL,
    metric_scope    ENUM('dayahead','realtime','delivery','benchmark') NOT NULL,
    pred_stage      VARCHAR(32)     DEFAULT NULL,
    actual_source   VARCHAR(32)     DEFAULT NULL,
    smape           DECIMAL(10,4)   DEFAULT NULL,
    mae             DECIMAL(10,4)   DEFAULT NULL,
    rmse            DECIMAL(10,4)   DEFAULT NULL,
    mape            DECIMAL(10,4)   DEFAULT NULL,
    wmape           DECIMAL(10,4)   DEFAULT NULL,
    evaluable_days  INT             DEFAULT NULL,
    evaluable_hours INT             DEFAULT NULL,
    config_json     JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (metric_run_id),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE SET NULL,
    INDEX idx_scope (metric_scope),
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── Read view: shadow safety projection (3NF-aware) ──────────────────
-- Used by backend/app/services/report_service.shadow_safety(). The stored
-- base tables are 3NF; this view re-projects the denormalized shape the
-- report layer expects (stage names resolved via efm_dim_stage).

CREATE OR REPLACE VIEW v_efm_shadow_safety AS
SELECT
    (SELECT COUNT(*) FROM efm_predictions
        WHERE is_shadow=1 AND is_selected=1) AS shadow_selected_count,
    (SELECT COUNT(*) FROM efm_predictions p
        JOIN efm_dim_stage s ON p.stage_id = s.id
        WHERE p.is_selected=1
          AND s.name IN ('selector_shadow','p3_shadow','extreme_price_shadow','shadow')
    ) AS final_from_shadow_count,
    (SELECT COUNT(*) FROM efm_runs
        WHERE status='FAIL' OR delivery_status='FAILED_NO_DELIVERY'
    ) AS unsafe_run_count;

SET FOREIGN_KEY_CHECKS = 1;
