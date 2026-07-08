export interface PredictionRow {
  run_id: string;
  target_date?: string;
  hour_business: number;
  task?: string;
  stage?: string;
  model_name?: string;
  model_version?: string;
  pred_price?: number;
  is_shadow: boolean;
  is_selected: boolean;
  selected_reason?: string;
}

export interface PredictionCompareItem {
  run_id: string;
  target_date?: string;
  hour_business: number;
  model_name: string;
  stage?: string;
  pred_price?: number;
  is_selected: boolean;
  is_shadow: boolean;
}

export interface SelectedPrediction {
  run_id: string;
  target_date?: string;
  hour_business: number;
  stage?: string;
  model_name?: string;
  pred_price?: number;
  selected_reason?: string;
}
