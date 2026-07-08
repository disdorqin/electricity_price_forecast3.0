interface Props {
  status?: string;
}

function tone(status?: string): "ok" | "warn" | "bad" | "muted" {
  const s = (status || "").toUpperCase();
  if (s === "SAFE" || s === "OK" || s === "COMPLETE" || s === "READY" || s === "NORMAL")
    return "ok";
  if (s === "UNSAFE" || s === "FAIL" || s === "FAILED_NO_DELIVERY") return "bad";
  if (s === "PARTIAL" || s === "DEGRADED_DELIVERED" || s === "BUILDING") return "warn";
  return "muted";
}

export default function SafetyBadge({ status }: Props) {
  const t = tone(status);
  return <span className={`badge ${t}`}>{status || "—"}</span>;
}
