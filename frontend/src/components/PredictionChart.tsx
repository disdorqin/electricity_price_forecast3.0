import { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface Item {
  hour_business: number;
  stage?: string;
  model_name?: string;
  pred_price?: number;
  is_selected: boolean;
  is_shadow: boolean;
}

const STAGE_COLORS: Record<string, string> = {
  da_anchor: "#38bdf8",
  official_baseline: "#a78bfa",
  seasonal_da_router: "#22c55e",
  selector_shadow: "#f59e0b",
  p3_shadow: "#fb7185",
  final_selected: "#22c55e",
};

export default function PredictionChart({ data }: { data: Item[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, "dark");
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 50, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category", name: "Hour", data: [] },
      yAxis: { type: "value", name: "Price (CNY/MWh)" },
      series: [],
    });

    const stages = Array.from(new Set(data.map((d) => d.stage || d.model_name || "unknown")));
    const hours = Array.from(new Set(data.map((d) => d.hour_business))).sort(
      (a, b) => Number(a) - Number(b)
    );

    const series = stages.map((stage) => {
      const rows = data.filter((d) => (d.stage || d.model_name) === stage);
      const isShadow = rows.some((r) => r.is_shadow);
      const isSelected = rows.some((r) => r.is_selected);
      const map: Record<number, number | null> = {};
      hours.forEach((h) => (map[Number(h)] = null));
      rows.forEach((r) => (map[Number(r.hour_business)] = r.pred_price ?? null));
      return {
        name: stage,
        type: "line",
        data: hours.map((h) => map[Number(h)]),
        symbol: "circle",
        symbolSize: isSelected ? 8 : 5,
        lineStyle: {
          width: isSelected ? 4 : 2,
          type: isShadow ? "dashed" : "solid",
        },
        itemStyle: { color: STAGE_COLORS[stage] || "#94a3b8" },
        emphasis: { focus: "series" },
      };
    });

    chart.setOption({
      xAxis: { type: "category", data: hours.map(String) },
      series,
    });
    return () => chart.dispose();
  }, [data]);

  return <div className="chart" ref={ref} />;
}
