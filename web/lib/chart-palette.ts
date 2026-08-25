/**
 * The one place chart colours come from.
 *
 * Every value is a CSS variable from app/globals.css, so a bar drawn with
 * `CHART.positive` is the same green as the "+3,2 %" next to it, and both
 * follow the light/dark theme without any JavaScript. recharts passes these
 * straight into SVG `fill`/`stroke`, where `var()` works.
 *
 * Same rule as everywhere else: colour means one thing. positive = money in /
 * delivered, warning = watch, negative = money out / returned, accent = the
 * brand's own series, neutral = context.
 */

export const CHART = {
  accent: "var(--color-series-1)",
  positive: "var(--color-series-2)",
  warning: "var(--color-series-3)",
  negative: "var(--color-series-4)",
  violet: "var(--color-series-5)",
  teal: "var(--color-series-6)",
  neutral: "var(--color-series-7)",
  positive2: "var(--color-positive-2)",
  neutralBar: "var(--color-neutral-bar)",
  dim: "var(--color-ink-dim)",
  /** Ordered series for charts with N lines. */
  series: [1, 2, 3, 4, 5, 6, 7].map((n) => `var(--color-series-${n})`),
  grid: "var(--color-line)",
  axis: "var(--color-ink-muted)",
  cursor: "var(--color-hover-strong)",
} as const;

/** Nothing on a chart is smaller than the body text of a table. */
export const CHART_FONT = { tick: 13, legend: 13, label: 13 } as const;

/** Minimum heights; a chart shorter than 240px is unreadable on a phone. */
export const CHART_HEIGHT = { sm: 240, md: 280, lg: 320 } as const;

export const AXIS_PROPS = {
  axisLine: false,
  tickLine: false,
  tick: { fill: CHART.axis, fontSize: CHART_FONT.tick },
} as const;

export const GRID_PROPS = {
  stroke: CHART.grid,
  strokeDasharray: "3 3",
  vertical: false,
} as const;

/** Inline styles for the default recharts tooltip, matching the Card look. */
export const TOOLTIP_STYLE = {
  contentStyle: {
    background: "var(--color-surface)",
    border: "1px solid var(--color-line)",
    borderRadius: 10,
    boxShadow: "var(--shadow-pop)",
    fontSize: CHART_FONT.label,
    color: "var(--color-ink)",
  },
  labelStyle: { color: "var(--color-ink-muted)", fontSize: CHART_FONT.label, fontWeight: 600 },
  itemStyle: { fontSize: CHART_FONT.label },
} as const;
