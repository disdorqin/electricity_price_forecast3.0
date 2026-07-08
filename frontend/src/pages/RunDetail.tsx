import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import RunTimeline from "../components/RunTimeline";
import PostflightPanel from "../components/PostflightPanel";
import HourlyTable from "../components/HourlyTable";
import StatusCard from "../components/StatusCard";

export default function RunDetail() {
  const { runId } = useParams();
  const [detail, setDetail] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [preds, setPreds] = useState<any[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!runId) return;
    Promise.all([
      api.getRun(runId).catch(() => null),
      api.getRunEvents(runId).catch(() => []),
      api.getPredictions(runId).catch(() => []),
    ])
      .then(([d, e, p]) => {
        setDetail(d);
        setEvents(e || []);
        setPreds(p || []);
      })
      .catch((e) => setErr(String(e)));
  }, [runId]);

  if (err) return <div className="warn-box">{err}</div>;
  if (!detail) return <div className="muted">Loading…</div>;

  return (
    <div>
      <h2>Run Detail · {runId}</h2>
      <div className="grid" style={{ marginBottom: 16 }}>
        <StatusCard title="Predictions" value={preds.length} />
        <StatusCard
          title="Selected"
          value={preds.filter((p) => p.is_selected).length}
        />
        <StatusCard title="Shadow" value={preds.filter((p) => p.is_shadow).length} />
        <StatusCard title="Events" value={events.length} />
      </div>

      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <RunTimeline summary={detail} events={events} />
        </div>
        <div style={{ flex: 1 }}>
          <h3>Postflight</h3>
          <PostflightPanel checks={detail.postflight || []} />
          <h3>Delivery Outputs</h3>
          <HourlyTable
            rows={(detail.delivery || []).map((d: any) => ({
              hour_business: 0,
              stage: d.output_type,
              model_name: d.output_path,
              pred_price: d.row_count,
              is_selected: true,
              is_shadow: false,
            }))}
          />
        </div>
      </div>
    </div>
  );
}
