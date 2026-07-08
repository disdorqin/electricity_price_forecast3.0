import SafetyBadge from "./SafetyBadge";

interface Check {
  check_name: string;
  passed: boolean;
  details?: string;
}

export default function PostflightPanel({ checks }: { checks: Check[] }) {
  if (!checks || checks.length === 0)
    return <div className="muted">No postflight checks.</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>Check</th>
          <th>Result</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {checks.map((c, i) => (
          <tr key={i}>
            <td>{c.check_name}</td>
            <td>
              <SafetyBadge status={c.passed ? "OK" : "FAIL"} />
            </td>
            <td className="muted">{c.details || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
