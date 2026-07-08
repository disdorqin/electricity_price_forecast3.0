import { useEffect, useState } from "react";
import { api } from "../api/client";
import StatusCard from "../components/StatusCard";
import SafetyBadge from "../components/SafetyBadge";

export default function Dashboard() {
  const [health, setHealth] = useState<any>(null);
  const [db, setDb] = useState<any>(null);
  const [shadow, setShadow] = useState<any>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      api.health().catch((e) => setErr(String(e))),
      api.healthDb().catch(() => {}),
      api.shadowSafety().catch(() => {}),
      api.listRuns(7).catch(() => []),
    ])
      .then(([h, d, s, r]) => {
        setHealth(h); setDb(d); setShadow(s); setRuns(r || []);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <h2>Dashboard</h2>
      {err && <div className="warn-box">{err}</div>}
      <div className="grid">
        <StatusCard title="Backend" value={health?.status || "—"} tone={health?.status === "ok" ? "ok" : "warn"} />
        <StatusCard title="DB" value={db?.status || "—"} tone={db?.status === "ok" ? "ok" : "warn"} />
        <StatusCard
          title="DB Tables"
          value={db?.table_count ?? "—"}
        />
        <StatusCard
          title="Shadow Safety"
          value={shadow?.status || "—"}
          tone={shadow?.status === "SAFE" ? "ok" : shadow ? "bad" : "muted"}
        />
        <StatusCard title="Shadow Selected" value={shadow?.shadow_selected_count ?? "—"} />
        <StatusCard title="Unsafe Runs" value={shadow?.unsafe_run_count ?? "—"} />
      </div>

      <h3 style={{ marginTop: 24 }}>Recent Runs</h3>
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Target</th>
            <th>Mode</th>
            <th>Status</th>
            <th>Delivery</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td>{r.run_id}</td>
              <td>{r.target_date}</td>
              <td>{r.mode}</td>
              <td><SafetyBadge status={r.status} /></td>
              <td><SafetyBadge status={r.delivery_status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
