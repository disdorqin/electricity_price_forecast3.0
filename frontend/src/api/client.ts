// Minimal API client for the EFM3 Control Plane backend.
// Base URL is configurable via VITE_API_BASE (default: same-origin /api).

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) || "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail || "";
    } catch {
      detail = await res.text();
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<any>("/api/health"),
  healthDb: () => request<any>("/api/health/db"),
  healthSchema: () => request<any>("/api/health/schema"),

  listRuns: (limit = 50, mode?: string) =>
    request<any[]>(`/api/runs?limit=${limit}${mode ? `&mode=${mode}` : ""}`),
  getRun: (runId: string) => request<any>(`/api/runs/${runId}`),
  getRunSummary: (runId: string) => request<any>(`/api/runs/${runId}/summary`),
  getRunEvents: (runId: string) => request<any[]>(`/api/runs/${runId}/events`),
  getRunPostflight: (runId: string) => request<any[]>(`/api/runs/${runId}/postflight`),
  getRunDelivery: (runId: string) => request<any[]>(`/api/runs/${runId}/delivery-outputs`),

  getPredictions: (runId: string) =>
    request<any[]>(`/api/runs/${runId}/predictions`),
  getPredictionsHourly: (runId: string) =>
    request<any[]>(`/api/runs/${runId}/predictions/hourly`),
  getPredictionsSelected: (runId: string) =>
    request<any[]>(`/api/runs/${runId}/predictions/selected`),
  getPredictionsCompare: (runId: string, models: string[]) =>
    request<any[]>(
      `/api/runs/${runId}/predictions/compare?models=${encodeURIComponent(models.join(","))}`
    ),

  listDatasets: (limit = 50) => request<any[]>(`/api/datasets?limit=${limit}`),
  getDataset: (id: string) => request<any>(`/api/datasets/${id}`),
  getLatestDataset: (targetDate?: string) =>
    request<any>(`/api/datasets/latest${targetDate ? `?target_date=${targetDate}` : ""}`),
  listDataSources: () => request<any[]>(`/api/data-sources`),
  listSourceFiles: (limit = 200) => request<any[]>(`/api/source-files?limit=${limit}`),
  listDataUpdateRuns: (limit = 50) => request<any[]>(`/api/data-update-runs?limit=${limit}`),

  shadowSafety: () => request<any>("/api/reports/shadow-safety"),
  dbHealth: () => request<any>("/api/reports/db-health"),

  lineageRun: (runId: string) => request<any>(`/api/lineage/${runId}`),
  lineageHour: (runId: string, hour: number) =>
    request<any>(`/api/lineage/${runId}/hour/${hour}`),

  ops: (action: string, body: Record<string, unknown>) =>
    request<any>(`/api/ops/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
