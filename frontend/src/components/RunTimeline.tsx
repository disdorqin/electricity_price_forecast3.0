interface Event {
  event_type?: string;
  event_name?: string;
  event_detail?: string;
  created_at?: string;
}

export default function RunTimeline({
  summary,
  events,
}: {
  summary?: Record<string, unknown>;
  events?: Event[];
}) {
  return (
    <div>
      {summary && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="title">Run</div>
          <div>
            <b>{String(summary.run_id)}</b> · {String(summary.target_date)} · mode={String(
              summary.mode
            )}
          </div>
          <div className="muted">
            status={String(summary.status)} · delivery={String(summary.delivery_status)} ·
            exit={String(summary.exit_code)}
          </div>
        </div>
      )}
      <h3>Events</h3>
      {!events || events.length === 0 ? (
        <div className="muted">No events recorded.</div>
      ) : (
        <ul>
          {events.map((e, i) => (
            <li key={i}>
              <b>[{e.event_type}]</b> {e.event_name}
              <span className="muted"> — {e.event_detail || ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
