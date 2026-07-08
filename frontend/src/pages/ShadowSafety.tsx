import { useEffect, useState } from "react";
import { api } from "../api/client";
import StatusCard from "../components/StatusCard";
import SafetyBadge from "../components/SafetyBadge";

export default function ShadowSafety() {
  const [shadow, setShadow] = useState<any>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.shadowSafety().then(setShadow).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="warn-box">{err}</div>;
  if (!shadow) return <div className="muted">Loading…</div>;

  return (
    <div>
      <h2>Shadow Safety</h2>
      <div className="grid">
        <StatusCard
          title="Overall"
          value={shadow.status}
          tone={shadow.status === "SAFE" ? "ok" : "bad"}
        />
        <StatusCard title="Shadow Selected" value={shadow.shadow_selected_count} />
        <StatusCard title="Final From Shadow" value={shadow.final_from_shadow_count} />
        <StatusCard title="Unsafe Runs" value={shadow.unsafe_run_count} />
      </div>

      <h3>Stop Gates</h3>
      <ul>
        <li>
          Shadow predictions must never become the final selected prediction.{" "}
          <SafetyBadge status={shadow.shadow_selected_count === 0 ? "OK" : "FAIL"} />
        </li>
        <li>
          Any run with status FAIL or FAILED_NO_DELIVERY blocks delivery.{" "}
          <SafetyBadge status={shadow.unsafe_run_count === 0 ? "OK" : "FAIL"} />
        </li>
        <li>
          P3 / selector shadow status is audited per run and shown in the Lineage Graph.
        </li>
      </ul>

      {shadow.status !== "SAFE" && (
        <div className="warn-box">
          ⚠️ Shadow safety check FAILED. Do not promote this run to formal delivery.
        </div>
      )}
    </div>
  );
}
