export interface DataSource {
  source_id: string;
  source_name?: string;
  source_type?: string;
  market?: string;
  root_path?: string;
  enabled?: boolean;
}

export interface SourceFile {
  file_name?: string;
  file_path?: string;
  file_ext?: string;
  file_size?: number;
  file_sha256?: string;
  import_status?: string;
  import_message?: string;
  detected_at?: string;
  imported_at?: string;
}

export interface DataUpdateRun {
  update_run_id: string;
  target_date?: string;
  mode?: string;
  status?: string;
  files_detected?: number;
  files_imported?: number;
  rows_imported?: number;
  started_at?: string;
  finished_at?: string;
}

export interface DatasetVersion {
  dataset_id: string;
  target_date?: string;
  market?: string;
  source_file_hashes?: unknown;
  row_counts?: unknown;
  canonical_hour_mapping?: boolean;
  leakage_cutoff?: string;
  status?: string;
  created_at?: string;
}

export interface DatasetReadiness {
  dataset_id: string;
  target_date?: string;
  status?: string;
  row_counts?: unknown;
  leakage_cutoff?: string;
  canonical_hour_mapping?: boolean;
}

export interface LineageNode {
  node_id?: string;
  node_type: string;
  label: string;
  detail?: unknown;
}

export interface LineageEdge {
  from_node: string;
  to_node: string;
}

export interface LineageResponse {
  run_id: string;
  hour_business: number;
  target_date?: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
  router_decision?: unknown;
  selected_reason?: string;
  is_shadow: boolean;
  shadow_safe?: boolean;
}

export interface ShadowSafety {
  status: string;
  shadow_selected_count: number;
  final_from_shadow_count: number;
  unsafe_run_count: number;
  detail?: string;
}

export interface DbHealth {
  status: string;
  db_url_prefix?: string;
  table_count?: number;
  tables?: string[];
  note?: string;
}

export interface Health {
  status: string;
  service: string;
  app_env?: string;
  db_configured: boolean;
  ops_enabled: boolean;
  timestamp?: string;
}
