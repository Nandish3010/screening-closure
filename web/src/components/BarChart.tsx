/** Small accessible SVG bar chart. Colours come from CSS variables so light and dark both read well. */
export interface Bar {
  label: string;
  value: number;
}

export function BarChart({
  title, data, unit, colorVar, horizontal,
}: {
  title: string;
  data: Bar[];
  unit: string;
  colorVar: string;
  horizontal?: boolean;
}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const summary = data.map((d) => `${d.label}: ${d.value} ${unit}`).join("; ");

  if (horizontal) {
    const rowH = 34;
    const labelW = 170;
    const w = 420;
    const h = data.length * rowH + 8;
    return (
      <figure className="chart" data-testid={`chart-${title}`}>
        <figcaption>{title}<small>Unit: {unit}. Synthetic data.</small></figcaption>
        <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${title}. ${summary}`} className="svg-chart">
          {data.map((d, i) => {
            const bw = ((w - labelW - 40) * d.value) / max;
            return (
              <g key={d.label} transform={`translate(0 ${i * rowH + 4})`}>
                <text x={labelW - 8} y={rowH / 2} textAnchor="end" dominantBaseline="middle" className="chart-label">{d.label}</text>
                <rect x={labelW} y={4} width={Math.max(bw, 1)} height={rowH - 12} rx={3} style={{ fill: `var(${colorVar})` }} />
                <text x={labelW + bw + 6} y={rowH / 2} dominantBaseline="middle" className="chart-value">{d.value}</text>
              </g>
            );
          })}
        </svg>
      </figure>
    );
  }

  const w = 420;
  const h = 220;
  const padB = 36;
  const padT = 20;
  const bw = (w - 20) / data.length;
  return (
    <figure className="chart" data-testid={`chart-${title}`}>
      <figcaption>{title}<small>Unit: {unit}. Synthetic data.</small></figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${title}. ${summary}`} className="svg-chart">
        <line x1={10} x2={w - 10} y1={h - padB} y2={h - padB} className="chart-axis" />
        {data.map((d, i) => {
          const bh = ((h - padB - padT) * d.value) / max;
          const x = 10 + i * bw + bw * 0.15;
          return (
            <g key={d.label}>
              <rect x={x} y={h - padB - bh} width={bw * 0.7} height={Math.max(bh, 1)} rx={3} style={{ fill: `var(${colorVar})` }} />
              <text x={x + bw * 0.35} y={h - padB - bh - 6} textAnchor="middle" className="chart-value">{d.value}</text>
              <text x={x + bw * 0.35} y={h - padB + 18} textAnchor="middle" className="chart-label">{d.label}</text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
