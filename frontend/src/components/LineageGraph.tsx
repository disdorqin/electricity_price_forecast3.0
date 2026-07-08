import SafetyBadge from "./SafetyBadge";

interface Node {
  node_id?: string;
  node_type: string;
  label: string;
  detail?: unknown;
}

interface Edge {
  from_node: string;
  to_node: string;
}

interface Props {
  lineage: {
    run_id: string;
    hour_business: number;
    target_date?: string;
    nodes: Node[];
    edges: Edge[];
    router_decision?: unknown;
    selected_reason?: string;
    is_shadow: boolean;
    shadow_safe?: boolean;
  };
}

// Simplified lineage node graph — no graph DB, just ordered nodes + edges.
export default function LineageGraph({ lineage }: Props) {
  const byId: Record<string, Node> = {};
  lineage.nodes.forEach((n) => {
    byId[n.node_id || n.label] = n;
  });

  return (
    <div className="lineage-graph">
      <div className="row" style={{ alignItems: "center" }}>
        <b>
          Lineage · run {lineage.run_id} · H{lineage.hour_business}
        </b>
        <SafetyBadge status={lineage.shadow_safe ? "SAFE" : "UNSAFE"} />
      </div>
      {lineage.nodes.map((n, i) => {
        const cls =
          n.node_type === "router"
            ? "ln-node router"
            : n.node_type === "selected"
            ? "ln-node selected"
            : n.node_type === "candidate" && (n.detail as any)?.is_shadow
            ? "ln-node shadow"
            : "ln-node";
        return (
          <div key={i}>
            {i > 0 && <div className="ln-edge">↓</div>}
            <div className={cls}>
              <span className="muted">[{n.node_type}]</span> {n.label}
            </div>
          </div>
        );
      })}
      {lineage.selected_reason && (
        <div className="muted">Selected reason: {lineage.selected_reason}</div>
      )}
    </div>
  );
}
