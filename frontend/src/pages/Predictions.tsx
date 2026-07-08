import { useEffect, useState } from "react";
import { api } from "../api/client";
import PredictionChart from "../components/PredictionChart";
import HourlyTable from "../components/HourlyTable";
import LineageGraph from "../components/LineageGraph";

const DEFAULT_MODELS = ["da_anchor", "official_baseline", "seasonal_da_router"];

export default function Predictions() {
  const [runId, setRunId] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [compare, setCompare] = useState<any[]>([]);
  const [hourly, setHourly] = useState<any[]>([]);
  const [selected, setSelected] = useState<any[]>([]);
  const [hour, setHour] = useState(12);
  const [lineage, setLineage] = useState<any>(null);
  const [showShadow, setShowShadow] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.listRuns(20).then(setRuns).catch(() => []);
  }, []);

  useEffect(() => {
    if (!runId) return;
    setErr("");
    Promise.all([
      api.getPredictionsCompare(runId, DEFAULT_MODELS).catch(() => []),
      api.getPredictionsHourly(runId).catch(() => []),
      api.getPredictionsSelected(runId).catch(() => []),
    ])
      .then(([c, h, s]) => {
        setCompare(c || []);
        setHourly(h || []);
        setSelected(s || []);
      })
      .catch((e) => setErr(String(e)));
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    api.lineageHour(runId, hour).then(setLineage).catch(() => null);
  }, [runId, hour]);

  const shownHourly = showShadow
    ? hourly
    : hourly.filter((h) => !h.is_shadow);

  return (
    <div>
      <h2>Predictions</h2>
      {err && <div className="warn-box">{err}</div>}
      <div className="row" style={{ alignItems: "center", marginBottom: 12 }}>
        <select value={runId} onChange={(e) => setRunId(e.target.value)}>
          <option value="">Select a run…</option>
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id} ({r.target_date})
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={showShadow}
            onChange={(e) => setShowShadow(e.target.checked)}
          />{" "}
          Show shadow predictions
        </label>
      </div>

      {compare.length > 0 && (
        <>
          <h3>Hourly Price Curve (1–24)</h3>
          <PredictionChart data={compare} />
        </>
      )}

      {shownHourly.length > 0 && (
        <>
          <h3>Predictions Table</h3>
          <HourlyTable rows={shownHourly} />
        </>
      )}

      {selected.length > 0 && (
        <>
          <h3>Selected Final</h3>
          <HourlyTable rows={selected} />
        </>
      )}

      <h3>Lineage Graph · Hour {hour}</h3>
      <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
        <label>
          Hour:{" "}
          <input
            type="number"
            min={1}
            max={24}
            value={hour}
            onChange={(e) => setHour(Number(e.target.value))}
            style={{ width: 60 }}
          />
        </label>
      </div>
      {lineage ? <LineageGraph lineage={lineage} /> : <div className="muted">No lineage.</div>}
    </div>
  );
}
