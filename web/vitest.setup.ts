import "@testing-library/jest-dom/vitest";

/**
 * Recharts' ResponsiveContainer observes its parent to pick a size. jsdom has
 * no ResizeObserver, so without this stub every chart widget throws on mount
 * and the failure looks like a bug in the widget rather than in the harness.
 */
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    configurable: true,
    value: ResizeObserverStub,
  });
}
