interface Props {
  title: string;
  value: string | number;
  tone?: "ok" | "warn" | "bad" | "muted";
}

export default function StatusCard({ title, value, tone = "muted" }: Props) {
  const toneClass =
    tone === "ok" ? "ok" : tone === "warn" ? "warn" : tone === "bad" ? "bad" : "muted";
  return (
    <div className="card">
      <div className="title">{title}</div>
      <div className={`value badge ${toneClass}`} style={{ fontSize: 22 }}>
        {value}
      </div>
    </div>
  );
}
