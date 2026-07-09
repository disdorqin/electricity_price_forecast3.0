-- EFM3 3.0 — Production Circuit Ledger V2 (migration 005)
--
-- Adds the structured tables required to express the FULL production
-- prediction circuit (dayahead chain → repair → fusion → classifier →
-- task_final, mirrored for realtime, then cross_task_fusion → separator →
-- delivery_final → postflight → metrics).
--
-- Design principles (MUST hold for every migration in this repo):
--   1. Never DROP or ALTER existing tables/columns. This migration only
--      CREATES new tables. Existing PR #12/#14/#15/#16 tables are untouched.
--   2. Every CREATE TABLE uses IF NOT EXISTS → safe to re-run.
--   3. MySQL 8 compatible (utf8mb4, InnoDB).
--   4. Indexes added for the hot query paths (run_id, target_date, task).
--   5. No data deletion. Idempotent by construction.
--
-- The production_circuit chain writes per-stage predictions into the
-- EXISTING efm_predictions table (reusing task/stage columns) AND records
-- the structured audit trail / lineage into the V2 tables below.

USE efm3;

-- ── 1. efm_pipeline_steps ───────────────────────────────────────────
-- One row per executed circuit step. Records execution status, I/O counts
-- and timing so the whole DAG is observable even when a step is skipped
-- or partially failed (e.g. realtime model output missing).
CREATE TABLE IF NOT EXISTS efm_pipeline_steps (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    task            ENUM('dayahead','realtime','fusion','delivery') NOT NULL DEFAULT 'dayahead',
    step_name       VARCHAR(64)     NOT NULL,
    step_order      SMALLINT        NOT NULL,
    status          ENUM('PENDING','RUNNING','COMPLETE','PARTIAL','FAIL','SKIPPED') NOT NULL DEFAULT 'PENDING',
    input_count     INT             DEFAULT NULL,
    output_count    INT             DEFAULT NULL,
    started_at      DATETIME(3)     DEFAULT NULL,
    finished_at     DATETIME(3)     DEFAULT NULL,
    runtime_ms      INT             DEFAULT NULL,
    message         VARCHAR(512)    DEFAULT NULL,
    config_json     JSON            DEFAULT NULL,
    metrics_json    JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run_step (run_id, step_order),
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_step_name (run_id, step_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 2. efm_prediction_batches ──────────────────────────────────────
-- Every group of 24-hour predictions is tracked as a batch so individual
-- model outputs, repairs and fusions are attributable to a source step.
CREATE TABLE IF NOT EXISTS efm_prediction_batches (
    batch_id        VARCHAR(96)     NOT NULL PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    task            ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    stage           VARCHAR(32)     NOT NULL,
    model_name      VARCHAR(64)     DEFAULT NULL,
    model_version   VARCHAR(32)     DEFAULT NULL,
    source_step     VARCHAR(64)     DEFAULT NULL,
    row_count       INT             NOT NULL DEFAULT 0,
    is_final_candidate BOOLEAN      NOT NULL DEFAULT FALSE,
    is_shadow       BOOLEAN         NOT NULL DEFAULT FALSE,
    artifact_id     VARCHAR(96)     DEFAULT NULL,
    batch_hash      VARCHAR(64)     DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    metadata_json   JSON            DEFAULT NULL,
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_run_stage (run_id, task, stage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 3. efm_prediction_lineage_edges ────────────────────────────────
-- Expresses raw → repaired → weighted → fused → final lineage.
CREATE TABLE IF NOT EXISTS efm_prediction_lineage_edges (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    parent_prediction_id BIGINT     DEFAULT NULL,
    child_prediction_id  BIGINT     DEFAULT NULL,
    relation_type   ENUM('repair','weight','fuse','select','fallback','classifier_adjust','separator_adjust','negative_fix') NOT NULL,
    relation_json   JSON            DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (child_prediction_id)  REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_child (child_prediction_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 4. efm_repair_decisions ────────────────────────────────────────
-- Every "查查修补 / 模块修补 / 分离器修补" is recorded here, whether or not
-- a value actually changed (no_op decisions are also logged).
CREATE TABLE IF NOT EXISTS efm_repair_decisions (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    task            ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    hour_business   TINYINT         NOT NULL,
    repair_stage    VARCHAR(48)     NOT NULL COMMENT 'module_repair|weighted_repair|separator_repair|no_op',
    source_prediction_id BIGINT    DEFAULT NULL,
    repaired_prediction_id BIGINT  DEFAULT NULL,
    rule_name       VARCHAR(64)     NOT NULL,
    before_value    DECIMAL(12,4)   DEFAULT NULL,
    after_value     DECIMAL(12,4)   DEFAULT NULL,
    reason          VARCHAR(255)    DEFAULT NULL,
    severity        ENUM('info','warning','critical') NOT NULL DEFAULT 'info',
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (source_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    FOREIGN KEY (repaired_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_run_hour (run_id, target_date, hour_business)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 5. efm_fusion_candidates ───────────────────────────────────────
-- All candidates, their weights, ranks and selection outcome are retained
-- so fusion is fully auditable (including single-candidate fusion).
CREATE TABLE IF NOT EXISTS efm_fusion_candidates (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    task            ENUM('dayahead','realtime','fusion','delivery') NOT NULL,
    hour_business   TINYINT         NOT NULL,
    candidate_prediction_id BIGINT  DEFAULT NULL,
    candidate_model VARCHAR(64)     NOT NULL,
    candidate_stage VARCHAR(32)     NOT NULL,
    weight_value   DECIMAL(10,6)    DEFAULT NULL,
    rank_value     INT             DEFAULT NULL,
    score_json     JSON            DEFAULT NULL,
    selected        BOOLEAN         NOT NULL DEFAULT FALSE,
    rejected_reason VARCHAR(255)    DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_run_hour (run_id, target_date, hour_business),
    INDEX idx_selected (run_id, target_date, selected)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 6. efm_task_finals ──────────────────────────────────────────────
-- Authoritative "day-ahead final" and "real-time final", kept SEPARATE so
-- they are never silently mixed into a single blended row.
CREATE TABLE IF NOT EXISTS efm_task_finals (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    task            ENUM('dayahead','realtime') NOT NULL,
    hour_business   TINYINT         NOT NULL,
    final_prediction_id BIGINT     DEFAULT NULL,
    final_stage     VARCHAR(32)     NOT NULL,
    final_price     DECIMAL(12,4)   NOT NULL,
    source_policy   VARCHAR(64)     DEFAULT NULL,
    confidence_score DECIMAL(6,4)   DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (final_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    UNIQUE KEY uk_run_task_hour (run_id, target_date, task, hour_business),
    INDEX idx_run_target (run_id, target_date),
    INDEX idx_task (run_id, task)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 7. efm_delivery_finals ──────────────────────────────────────────
-- Final delivered price, with explicit provenance (which day-ahead final
-- and/or real-time final fed it, and what policy/separator rule applied).
CREATE TABLE IF NOT EXISTS efm_delivery_finals (
    id              BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(64)     NOT NULL,
    target_date     DATE            NOT NULL,
    hour_business   TINYINT         NOT NULL,
    dayahead_final_id BIGINT        DEFAULT NULL,
    realtime_final_id BIGINT        DEFAULT NULL,
    delivery_prediction_id BIGINT  DEFAULT NULL,
    delivery_price  DECIMAL(12,4)   NOT NULL,
    delivery_policy VARCHAR(64)     NOT NULL,
    separator_rule  VARCHAR(64)     DEFAULT NULL,
    fallback_reason VARCHAR(255)    DEFAULT NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (dayahead_final_id) REFERENCES efm_task_finals(id) ON DELETE SET NULL,
    FOREIGN KEY (realtime_final_id) REFERENCES efm_task_finals(id) ON DELETE SET NULL,
    FOREIGN KEY (delivery_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    UNIQUE KEY uk_run_hour (run_id, target_date, hour_business),
    INDEX idx_run_target (run_id, target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 8. efm_metric_runs ─────────────────────────────────────────────
-- Persisted metrics. Replaces the "metrics only live in markdown" pattern.
-- metric_scope clearly separates benchmark from production metrics so a
-- benchmark (DA anchor vs RT actual) can NEVER be reported as a model metric.
CREATE TABLE IF NOT EXISTS efm_metric_runs (
    metric_run_id   VARCHAR(96)     NOT NULL PRIMARY KEY,
    run_id          VARCHAR(64)     DEFAULT NULL,
    target_date_start DATE          NOT NULL,
    target_date_end   DATE          NOT NULL,
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
    INDEX idx_scope (metric_scope),
    INDEX idx_run (run_id),
    INDEX idx_range (target_date_start, target_date_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
