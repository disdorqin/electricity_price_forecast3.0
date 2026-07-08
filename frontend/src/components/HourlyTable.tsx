interface Row {
  hour_business: number;
  stage?: string;
  model_name?: string;
  pred_price?: number;
  is_selected: boolean;
  is_shadow: boolean;
  selected_reason?: string;
}

export default function HourlyTable({ rows }: { rows: Row[] }) {
  if (!rows || rows.length === 0) return <div className="muted">No predictions.</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>Hour</th>
          <th>Stage</th>
          <th>Model</th>
          <th>Price</th>
          <th>Selected</th>
          <th>Shadow</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td>{r.hour_business}</td>
            <td>{r.stage}</td>
            <td>{r.model_name}</td>
            <td>{r.pred_price}</td>
            <td>{r.is_selected ? "✅" : ""}</td>
            <td>{r.is_shadow ? "🌑" : ""}</td>
            <td className="muted">{r.selected_reason || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
