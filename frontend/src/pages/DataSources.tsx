import { useEffect, useState } from "react";
import { api } from "../api/client";
import SafetyBadge from "../components/SafetyBadge";

export default function DataSources() {
  const [sources, setSources] = useState<any[]>([]);
  const [files, setFiles] = useState<any[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [updates, setUpdates] = useState<any[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([
      api.listDataSources().catch(() => []),
      api.listSourceFiles(200).catch(() => []),
      api.listDatasets(50).catch(() => []),
      api.listDataUpdateRuns(50).catch(() => []),
    ])
      .then(([s, f, d, u]) => {
        setSources(s || []); setFiles(f || []); setDatasets(d || []); setUpdates(u || []);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="warn-box">{err}</div>;

  return (
    <div>
      <h2>Data Sources</h2>

      <h3>Data Sources</h3>
      <table>
        <thead>
          <tr><th>ID</th><th>Name</th><th>Type</th><th>Market</th><th>Enabled</th></tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.source_id}>
              <td>{s.source_id}</td><td>{s.source_name}</td><td>{s.source_type}</td>
              <td>{s.market}</td><td>{s.enabled ? "✅" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Source Files (sha256)</h3>
      <table>
        <thead>
          <tr><th>File</th><th>Ext</th><th>sha256</th><th>Status</th><th>Detected</th></tr>
        </thead>
        <tbody>
          {files.slice(0, 50).map((f, i) => (
            <tr key={i}>
              <td>{f.file_name}</td><td>{f.file_ext}</td>
              <td className="muted">{f.file_sha256?.slice(0, 12)}…</td>
              <td><SafetyBadge status={f.import_status} /></td>
              <td className="muted">{f.detected_at}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Dataset Versions</h3>
      <table>
        <thead>
          <tr><th>Dataset</th><th>Target</th><th>Status</th><th>Leakage Cutoff</th><th>Canonical</th></tr>
        </thead>
        <tbody>
          {datasets.map((d) => (
            <tr key={d.dataset_id}>
              <td>{d.dataset_id}</td><td>{d.target_date}</td>
              <td><SafetyBadge status={d.status} /></td>
              <td className="muted">{d.leakage_cutoff}</td>
              <td>{d.canonical_hour_mapping ? "✅" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Data Update Runs</h3>
      <table>
        <thead>
          <tr><th>ID</th><th>Target</th><th>Mode</th><th>Status</th><th>Files</th><th>Rows</th></tr>
        </thead>
        <tbody>
          {updates.map((u) => (
            <tr key={u.update_run_id}>
              <td>{u.update_run_id}</td><td>{u.target_date}</td><td>{u.mode}</td>
              <td><SafetyBadge status={u.status} /></td>
              <td>{u.files_imported}/{u.files_detected}</td><td>{u.rows_imported}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
