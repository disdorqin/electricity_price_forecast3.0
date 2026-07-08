export interface RunSummary {
  run_id: string;
  target_date: string;
  chain_version?: string;
  mode?: string;
  status?: string;
  delivery_status?: string;
  exit_code?: number;
  started_at?: string;
  finished_at?: string;
  duration_s?: number;
}

export interface RunEvent {
  event_type?: string;
  event_name?: string;
  event_detail?: string;
  created_at?: string;
}

export interface PostflightCheck {
  check_name: string;
  passed: boolean;
  details?: string;
}

export interface DeliveryOutput {
  output_type?: string;
  output_path?: string;
  row_count?: number;
  file_hash?: string;
}

export interface RunPredictionCounts {
  total: number;
  selected: number;
  shadow: number;
}

export interface RunDetail {
  summary?: RunSummary;
  prediction_counts?: RunPredictionCounts;
  postflight?: PostflightCheck[];
  delivery?: DeliveryOutput[];
  events_count?: number;
}
