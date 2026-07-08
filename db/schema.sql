-- EFM3 3.0 Production Ledger — MySQL Schema
-- 
-- This schema defines the production database for the EFM3 electricity
-- price forecasting system. All predictions, decisions, and audit trails
-- are stored here. CSV outputs are export artifacts only — MySQL is the
-- source of truth.
--
-- hour_business convention (canonical):
--   01:00 → 1  |  02:00 → 2  |  ...  |  23:00 → 23  |  00:00 → 24

CREATE DATABASE IF NOT EXISTS efm3 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE efm3;

-- ── 1. efm_runs ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_runs (
    run_id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date         DATE            NOT NULL,
    chain_version       VARCHAR(32)     NOT NULL DEFAULT '3.0-db-ledger-v1',
    mode                ENUM('dry_run','shadow','formal') NOT NULL DEFAULT 'dry_run',
    git_sha             VARCHAR(40)     DEFAULT NULL,
    config_hash         VARCHAR(64)     DEFAULT NULL,
    status              ENUM('PENDING','RUNNING','COMPLETE','PARTIAL','FAIL','CANCELLED') NOT NULL DEFAULT 'PENDING',
    delivery_status     ENUM('NORMAL','DEGRADED_DELIVERED','FAILED_NO_DELIVERY','NOT_ATTEMPTED') NOT NULL DEFAULT 'NOT_ATTEMPTED',
    exit_code           INT             NOT NULL DEFAULT 0,
    started_at          DATETIME(3)     DEFAULT NULL,
    finished_at         DATETIME(3)     DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    INDEX idx_target_date (target_date),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 2. efm_actual_prices ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_actual_prices (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    target_date         DATE            NOT NULL,
    hour_business       TINYINT         NOT NULL,
    period              VARCHAR(8)      GENERATED ALWAYS AS (
                            CASE WHEN hour_business BETWEEN 1 AND 8 THEN '1_8'
                                 WHEN hour_business BETWEEN 9 AND 16 THEN '9_16'
                                 ELSE '17_24' END
                        ) STORED,
    da_anchor           DECIMAL(12,4)   DEFAULT NULL,
    rt_actual           DECIMAL(12,4)   DEFAULT NULL,
    source_file         VARCHAR(255)    DEFAULT NULL,
    loaded_at           DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_date_hour (target_date, hour_business),
    INDEX idx_target_date (target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 3. efm_feature_snapshots ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_feature_snapshots (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    target_date         DATE            NOT NULL,
    hour_business       TINYINT         NOT NULL,
    feature_json        JSON            DEFAULT NULL,
    feature_hash        VARCHAR(64)     DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run_target (run_id, target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 4. efm_predictions ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_predictions (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    target_date         DATE            NOT NULL,
    hour_business       TINYINT         NOT NULL,
    task                ENUM('dayahead','realtime','fusion','final','shadow') NOT NULL,
    stage               VARCHAR(32)     NOT NULL COMMENT 'raw_model|da_anchor|official_baseline|selector_shadow|p3_shadow|seasonal_da_router|final_selected|etc',
    model_name          VARCHAR(64)     NOT NULL,
    model_version       VARCHAR(32)     DEFAULT 'unknown',
    pred_price          DECIMAL(12,4)   NOT NULL,
    is_shadow           BOOLEAN         NOT NULL DEFAULT FALSE,
    is_selected         BOOLEAN         NOT NULL DEFAULT FALSE,
    selected_reason     VARCHAR(255)    DEFAULT NULL,
    cutoff_time         DATETIME(3)     DEFAULT NULL,
    quality_flags       JSON            DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    -- One prediction per run×date×hour×stage (upsert key)
    UNIQUE KEY uk_run_date_hour_stage (run_id, target_date, hour_business, stage),
    INDEX idx_run_id (run_id),
    INDEX idx_run_selected (run_id, is_selected, task),
    INDEX idx_run_stage (run_id, target_date, stage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 5. efm_fusion_decisions ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_fusion_decisions (
    id                      BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id                  VARCHAR(64)     NOT NULL,
    target_date             DATE            NOT NULL,
    hour_business           TINYINT         NOT NULL,
    policy_name             VARCHAR(64)     NOT NULL,
    base_model              VARCHAR(64)     DEFAULT NULL,
    selected_model          VARCHAR(64)     NOT NULL,
    selected_prediction_id  BIGINT          DEFAULT NULL,
    decision_reason         VARCHAR(255)    DEFAULT NULL,
    decision_json           JSON            DEFAULT NULL,
    created_at              DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (selected_prediction_id) REFERENCES efm_predictions(id) ON DELETE SET NULL,
    UNIQUE KEY uk_run_date_hour (run_id, target_date, hour_business),
    INDEX idx_run_target (run_id, target_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 6. efm_postflight_checks ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_postflight_checks (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    target_date         DATE            NOT NULL,
    check_name          VARCHAR(64)     NOT NULL COMMENT 'row_count_24|hour_range|no_nan|no_duplicates|price_range|selected_source|shadow_not_final|submission_row_count',
    passed              BOOLEAN         NOT NULL,
    details             TEXT            DEFAULT NULL,
    checked_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run_check (run_id, check_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 7. efm_delivery_outputs ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_delivery_outputs (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    target_date         DATE            NOT NULL,
    output_type         VARCHAR(32)     NOT NULL COMMENT 'submission_ready|report|manifest',
    output_path         VARCHAR(512)    NOT NULL,
    file_hash           VARCHAR(64)     DEFAULT NULL,
    row_count           INT             DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 8. efm_model_registry ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_model_registry (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    model_name          VARCHAR(64)     NOT NULL,
    model_version       VARCHAR(32)     NOT NULL,
    task                ENUM('dayahead','realtime','fusion','shadow') NOT NULL,
    status              ENUM('active','shadow','deprecated','archived') NOT NULL DEFAULT 'shadow',
    description         TEXT            DEFAULT NULL,
    registered_at       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_name_version (model_name, model_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 9. efm_run_events ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_run_events (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    event_type          VARCHAR(32)     NOT NULL COMMENT 'start|step|warning|error|complete',
    event_name          VARCHAR(128)    NOT NULL,
    event_detail        TEXT            DEFAULT NULL,
    event_json          JSON            DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run (run_id),
    INDEX idx_type (run_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 10. efm_artifacts ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_artifacts (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id              VARCHAR(64)     NOT NULL,
    target_date         DATE            NOT NULL,
    artifact_type       VARCHAR(32)     NOT NULL COMMENT 'prediction_csv|report|model_binary|etc',
    file_path           VARCHAR(512)    NOT NULL,
    file_size_bytes     BIGINT          DEFAULT NULL,
    file_hash           VARCHAR(64)     DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (run_id) REFERENCES efm_runs(run_id) ON DELETE CASCADE,
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════════════
--  Data Ingestion Tables (002_data_ingestion)
-- ═══════════════════════════════════════════════════════════════════

-- ── 11. efm_data_sources ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_data_sources (
    source_id           VARCHAR(64)     NOT NULL PRIMARY KEY,
    source_name         VARCHAR(128)    NOT NULL,
    source_type         VARCHAR(32)     NOT NULL DEFAULT 'directory' COMMENT 'directory|http|db|custom',
    market              VARCHAR(32)     NOT NULL DEFAULT 'shandong',
    root_path           VARCHAR(512)    DEFAULT NULL,
    path_pattern        VARCHAR(255)    DEFAULT NULL,
    enabled             BOOLEAN         NOT NULL DEFAULT TRUE,
    config_json         JSON            DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 12. efm_source_files ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_source_files (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_id           VARCHAR(64)     NOT NULL,
    file_path           VARCHAR(1024)   NOT NULL,
    file_name           VARCHAR(255)    NOT NULL,
    file_ext            VARCHAR(16)     NOT NULL,
    file_size           BIGINT          DEFAULT NULL,
    file_mtime          DATETIME(3)     DEFAULT NULL,
    file_sha256         VARCHAR(64)     DEFAULT NULL,
    detected_at         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    imported_at         DATETIME(3)     DEFAULT NULL,
    import_status       ENUM('NEW','IMPORTED','SKIPPED','FAILED','CHANGED') NOT NULL DEFAULT 'NEW',
    import_message      TEXT            DEFAULT NULL,
    metadata_json       JSON            DEFAULT NULL,
    FOREIGN KEY (source_id) REFERENCES efm_data_sources(source_id) ON DELETE CASCADE,
    UNIQUE KEY uk_source_file (source_id, file_sha256(64), file_name(128)),
    INDEX idx_source (source_id),
    INDEX idx_status (import_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 13. efm_data_update_runs ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_data_update_runs (
    update_run_id       VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date         DATE            DEFAULT NULL,
    source_root         VARCHAR(512)    DEFAULT NULL,
    mode                ENUM('scan_only','incremental','full_refresh') NOT NULL DEFAULT 'incremental',
    status              ENUM('PENDING','SCANNING','IMPORTING','COMPLETE','PARTIAL','FAIL') NOT NULL DEFAULT 'PENDING',
    files_detected      INT             NOT NULL DEFAULT 0,
    files_imported      INT             NOT NULL DEFAULT 0,
    rows_imported       INT             NOT NULL DEFAULT 0,
    started_at          DATETIME(3)     DEFAULT NULL,
    finished_at         DATETIME(3)     DEFAULT NULL,
    message             TEXT            DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 14. efm_market_data_hourly ────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_market_data_hourly (
    id                  BIGINT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    market              VARCHAR(32)     NOT NULL DEFAULT 'shandong',
    data_type           VARCHAR(32)     NOT NULL COMMENT 'da_price|rt_price|fcast_wind|fcast_solar|etc',
    trade_date          DATE            NOT NULL,
    hour_business       TINYINT         NOT NULL,
    value               DECIMAL(16,4)   DEFAULT NULL,
    unit                VARCHAR(16)     DEFAULT 'CNY/MWh',
    source_file_id      BIGINT          DEFAULT NULL,
    quality_flags       JSON            DEFAULT NULL,
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (source_file_id) REFERENCES efm_source_files(id) ON DELETE SET NULL,
    UNIQUE KEY uk_market_type_date_hour (market, data_type, trade_date, hour_business),
    INDEX idx_trade_date (trade_date),
    INDEX idx_market_date (market, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 15. efm_dataset_versions ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_dataset_versions (
    dataset_id          VARCHAR(64)     NOT NULL PRIMARY KEY,
    target_date         DATE            NOT NULL,
    market              VARCHAR(32)     NOT NULL DEFAULT 'shandong',
    source_file_hashes  JSON            DEFAULT NULL,
    row_counts          JSON            DEFAULT NULL,
    canonical_hour_mapping BOOLEAN      NOT NULL DEFAULT TRUE,
    leakage_cutoff      DATETIME(3)     DEFAULT NULL,
    status              ENUM('READY','PARTIAL','FAIL','BUILDING') NOT NULL DEFAULT 'BUILDING',
    created_at          DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_target_date (target_date),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
