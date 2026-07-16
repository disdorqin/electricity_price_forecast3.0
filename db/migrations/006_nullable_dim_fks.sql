-- Migration 006: Make dimension FK columns nullable + migrate UKs to string columns
-- 
-- The production circuit code writes denormalized string values (stage, model_name, etc.)
-- while the DB has 3NF FK columns (stage_id, model_id, etc.). This migration makes
-- the FK columns nullable so the code can write string values without FK lookups.
--
-- Design: ALTER TABLE only, never DROP data. Safe to re-run.

USE efm3;

-- ── 1. efm_predictions ──────────────────────────────────────────
-- Drop FK constraints, modify columns to NULL, then drop/recreate UK
ALTER TABLE efm_predictions DROP FOREIGN KEY efm_predictions_ibfk_2;
ALTER TABLE efm_predictions DROP FOREIGN KEY efm_predictions_ibfk_3;
ALTER TABLE efm_predictions MODIFY COLUMN stage_id INT UNSIGNED NULL;
ALTER TABLE efm_predictions MODIFY COLUMN model_id INT UNSIGNED NULL;
ALTER TABLE efm_predictions ADD CONSTRAINT efm_predictions_ibfk_2 FOREIGN KEY (stage_id) REFERENCES efm_dim_stage(id);
ALTER TABLE efm_predictions ADD CONSTRAINT efm_predictions_ibfk_3 FOREIGN KEY (model_id) REFERENCES efm_dim_model(id);
-- Drop UK on FK columns, recreate on string columns
-- Note: existing data has NULL stage/model_name for ingestion rows, handle carefully
DROP INDEX uk_run_hour_stage_model ON efm_predictions;
-- Add UK on string columns (allow NULLs by using a partial approach)
-- Since MySQL treats NULLs as distinct in UNIQUE keys, rows with NULL stage/model_name won't conflict
ALTER TABLE efm_predictions ADD UNIQUE KEY uk_run_hour_stage_model (run_id(64), hour_business, stage(32), model_name(64));

-- ── 2. efm_pipeline_steps ───────────────────────────────────────
ALTER TABLE efm_pipeline_steps DROP FOREIGN KEY efm_pipeline_steps_ibfk_2;
ALTER TABLE efm_pipeline_steps MODIFY COLUMN step_name_id INT UNSIGNED NULL;
ALTER TABLE efm_pipeline_steps ADD CONSTRAINT efm_pipeline_steps_ibfk_2 FOREIGN KEY (step_name_id) REFERENCES efm_dim_step(id);

-- ── 3. efm_prediction_batches ──────────────────────────────────
ALTER TABLE efm_prediction_batches DROP FOREIGN KEY efm_prediction_batches_ibfk_2;
ALTER TABLE efm_prediction_batches MODIFY COLUMN stage_id INT UNSIGNED NULL;
ALTER TABLE efm_prediction_batches ADD CONSTRAINT efm_prediction_batches_ibfk_2 FOREIGN KEY (stage_id) REFERENCES efm_dim_stage(id);

-- ── 4. efm_task_finals ──────────────────────────────────────────
ALTER TABLE efm_task_finals DROP FOREIGN KEY efm_task_finals_ibfk_2;
ALTER TABLE efm_task_finals MODIFY COLUMN final_stage_id INT UNSIGNED NULL;
ALTER TABLE efm_task_finals ADD CONSTRAINT efm_task_finals_ibfk_2 FOREIGN KEY (final_stage_id) REFERENCES efm_dim_stage(id);

-- ── 5. efm_delivery_finals ─────────────────────────────────────
ALTER TABLE efm_delivery_finals DROP FOREIGN KEY efm_delivery_finals_ibfk_5;
ALTER TABLE efm_delivery_finals MODIFY COLUMN delivery_policy_id INT UNSIGNED NULL;
ALTER TABLE efm_delivery_finals ADD CONSTRAINT efm_delivery_finals_ibfk_5 FOREIGN KEY (delivery_policy_id) REFERENCES efm_dim_policy(id);

-- ── 6. efm_fusion_decisions ────────────────────────────────────
ALTER TABLE efm_fusion_decisions DROP FOREIGN KEY efm_fusion_decisions_ibfk_2;
ALTER TABLE efm_fusion_decisions DROP FOREIGN KEY efm_fusion_decisions_ibfk_4;
ALTER TABLE efm_fusion_decisions MODIFY COLUMN policy_id INT UNSIGNED NULL;
ALTER TABLE efm_fusion_decisions MODIFY COLUMN selected_model_id INT UNSIGNED NULL;
ALTER TABLE efm_fusion_decisions ADD CONSTRAINT efm_fusion_decisions_ibfk_2 FOREIGN KEY (policy_id) REFERENCES efm_dim_policy(id);
ALTER TABLE efm_fusion_decisions ADD CONSTRAINT efm_fusion_decisions_ibfk_4 FOREIGN KEY (selected_model_id) REFERENCES efm_dim_model(id);

-- ── 7. efm_prediction_lineage_edges ────────────────────────────
ALTER TABLE efm_prediction_lineage_edges DROP FOREIGN KEY efm_prediction_lineage_edges_ibfk_2;
ALTER TABLE efm_prediction_lineage_edges MODIFY COLUMN relation_id INT UNSIGNED NULL;
ALTER TABLE efm_prediction_lineage_edges ADD CONSTRAINT efm_prediction_lineage_edges_ibfk_2 FOREIGN KEY (relation_id) REFERENCES efm_dim_relation(id);

-- ── 8. efm_repair_decisions ────────────────────────────────────
ALTER TABLE efm_repair_decisions DROP FOREIGN KEY efm_repair_decisions_ibfk_2;
ALTER TABLE efm_repair_decisions DROP FOREIGN KEY efm_repair_decisions_ibfk_3;
ALTER TABLE efm_repair_decisions MODIFY COLUMN repair_stage_id INT UNSIGNED NULL;
ALTER TABLE efm_repair_decisions MODIFY COLUMN rule_id INT UNSIGNED NULL;
ALTER TABLE efm_repair_decisions ADD CONSTRAINT efm_repair_decisions_ibfk_2 FOREIGN KEY (repair_stage_id) REFERENCES efm_dim_repairstage(id);
ALTER TABLE efm_repair_decisions ADD CONSTRAINT efm_repair_decisions_ibfk_3 FOREIGN KEY (rule_id) REFERENCES efm_dim_rule(id);

-- ── 9. efm_fusion_candidates ───────────────────────────────────
ALTER TABLE efm_fusion_candidates DROP FOREIGN KEY efm_fusion_candidates_ibfk_2;
ALTER TABLE efm_fusion_candidates DROP FOREIGN KEY efm_fusion_candidates_ibfk_3;
ALTER TABLE efm_fusion_candidates MODIFY COLUMN candidate_model_id INT UNSIGNED NULL;
ALTER TABLE efm_fusion_candidates MODIFY COLUMN candidate_stage_id INT UNSIGNED NULL;
ALTER TABLE efm_fusion_candidates ADD CONSTRAINT efm_fusion_candidates_ibfk_2 FOREIGN KEY (candidate_model_id) REFERENCES efm_dim_model(id);
ALTER TABLE efm_fusion_candidates ADD CONSTRAINT efm_fusion_candidates_ibfk_3 FOREIGN KEY (candidate_stage_id) REFERENCES efm_dim_stage(id);
