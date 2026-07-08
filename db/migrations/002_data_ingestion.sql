-- EFM3 3.0 Data Ingestion Schema — Migration 002
--
-- Adds 5 tables for data source file registry, hourly market data,
-- data update runs, and dataset version tracking.
--
-- Builds on migration 001 (base EFM3 10 tables).

USE efm3;

-- ── 11. efm_data_sources ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS efm_data_sources (
    source_id           VARCHAR(64)     NOT NULL PRIMARY KEY,
    source_name         VARCHAR(128)    NOT NULL,
    source_type         VARCHAR(32)     NOT NULL DEFAULT 'directory',
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
