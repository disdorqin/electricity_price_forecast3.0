import { useState } from "react";
import { api } from "../api/client";

type OpName =
  | "init-db"
  | "update-data"
  | "run-dry-run"
  | "run-shadow-monitoring"
  | "export-submission"
  | "run-formal";

const DANGEROUS: OpName[] = ["export-submission", "run-formal"];

export default function OpsConsole() {
  const [targetDate, setTargetDate] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState<OpName | null>(null);
  const [pendingDanger, setPendingDanger] = useState<OpName | null>(null);
  const [err, setErr] = useState("");

  async function run(op: OpName, confirm = false) {
    setBusy(op);
    setErr("");
    setResult(null);
    try {
      const res = await api.ops(op, {
        target_date: targetDate || undefined,
        confirm,
      });
      setResult(res);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
      setPendingDanger(null);
    }
  }

  function onSafeClick(op: OpName) {
    if (DANGEROUS.includes(op)) {
      setPendingDanger(op);
      return;
    }
    run(op);
  }

  return (
    <div>
      <h2>Ops Console</h2>
      <p className="muted">
        All operations are whitelisted backend jobs. Dry-run / scan are safe.{" "}
        <b>Formal</b> and <b>export-submission</b> require explicit confirmation and are
        never executed silently.
      </p>

      <div className="card" style={{ marginBottom: 16 }}>
        <label>
          Target date (YYYY-MM-DD):{" "}
          <input
            type="date"
            value={targetDate}
            onChange={(e) => setTargetDate(e.target.value)}
          />
        </label>
      </div>

      <div className="cards">
        <button className="btn" disabled={busy !== null} onClick={() => run("init-db")}>
          Init DB
        </button>
        <button className="btn" disabled={busy !== null} onClick={() => onSafeClick("update-data")}>
          Update Data
        </button>
        <button className="btn" disabled={busy !== null || !targetDate} onClick={() => onSafeClick("run-dry-run")}>
          Run Dry-Run
        </button>
        <button className="btn" disabled={busy !== null || !targetDate} onClick={() => onSafeClick("run-shadow-monitoring")}>
          Run Shadow Monitoring
        </button>
      </div>

      <h3 style={{ marginTop: 20 }}>Dangerous Operations</h3>
      <div className="warn-box">
        ⚠️ The following write to the production ledger / produce submission files.
        They require a second confirmation.
      </div>
      <div className="cards">
        <button
          className="btn danger"
          disabled={busy !== null || !targetDate}
          onClick={() => onSafeClick("export-submission")}
        >
          Export Submission
        </button>
        <button
          className="btn danger"
          disabled={busy !== null || !targetDate}
          onClick={() => onSafeClick("run-formal")}
        >
          Run Formal
        </button>
      </div>

      {pendingDanger && (
        <div className="warn-box">
          <p>
            You are about to execute <b>{pendingDanger}</b> for <b>{targetDate}</b>. This is a
            production action and cannot be undone silently.
          </p>
          <button className="btn danger" onClick={() => run(pendingDanger, true)}>
            Confirm &amp; Execute
          </button>
          <button className="btn ghost" onClick={() => setPendingDanger(null)}>
            Cancel
          </button>
        </div>
      )}

      {err && <div className="warn-box">{err}</div>}
      {result && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="title">Result · {result.action}</div>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
