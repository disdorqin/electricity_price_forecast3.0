import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import SafetyBadge from "../components/SafetyBadge";

export default function Runs() {
  const [runs, setRuns] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.listRuns(50).then(setRuns).catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <h2>Runs</h2>
      {err && <div className="warn-box">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Target</th>
            <th>Mode</th>
            <th>Status</th>
            <th>Delivery</th>
            <th>Exit</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Actions</th>
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
              <td>{r.exit_code}</td>
              <td className="muted">{r.started_at}</td>
              <td className="muted">{r.finished_at}</td>
              <td>
                <button className="btn ghost" onClick={() => navigate(`/runs/${r.run_id}`)}>
                  Detail
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
