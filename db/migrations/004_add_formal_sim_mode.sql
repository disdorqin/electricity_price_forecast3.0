-- 004: Add formal_sim to efm_runs.mode ENUM

ALTER TABLE efm_runs
  MODIFY COLUMN mode ENUM('dry_run','shadow','formal','formal_sim')
  NOT NULL DEFAULT 'dry_run' COMMENT 'Run mode';
