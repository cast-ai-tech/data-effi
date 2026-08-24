import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationBell, bellLabel } from "@/components/NotificationBell";

/**
 * The bell's colour rules, from `globals.css`: an accent dot for "something
 * unread", a negative badge ONLY for critical, never warning. And the two
 * ways every menu in the shell closes: Escape and a click outside.
 */
const state = vi.hoisted(() => ({
  unreadCount: 0,
  criticalUnread: 0,
}));

vi.mock("@/lib/notifications", () => ({
  useNotifications: () => ({
    cursor: null,
    unreadCount: state.unreadCount,
    criticalUnread: state.criticalUnread,
    connected: true,
    subscribe: () => () => {},
    refreshCounts: () => {},
    markRead: async () => {},
    markAllRead: async () => {},
    setActive: () => {},
  }),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    get: vi.fn(async () => ({
      items: [],
      unread_count: 0,
      critical_unread_count: 0,
      next_before: null,
    })),
    post: vi.fn(async () => undefined),
    put: vi.fn(async () => undefined),
  },
  qs: (params: Record<string, unknown>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
    }
    const rendered = search.toString();
    return rendered ? `?${rendered}` : "";
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

beforeEach(() => {
  state.unreadCount = 0;
  state.criticalUnread = 0;
});

afterEach(cleanup);

describe("bellLabel", () => {
  it("says the counts in words a screen reader can read", () => {
    expect(bellLabel(0, 0)).toBe("Notificaciones, sin novedades");
    expect(bellLabel(3, 0)).toBe("Notificaciones, 3 sin leer");
    expect(bellLabel(3, 1)).toBe("Notificaciones, 3 sin leer (1 crítica)");
    expect(bellLabel(5, 2)).toBe("Notificaciones, 5 sin leer (2 críticas)");
  });
});

describe("NotificationBell", () => {
  it("is quiet with nothing unread", () => {
    render(<NotificationBell countryCode="CO" />);
    expect(screen.getByRole("button", { name: "Notificaciones, sin novedades" })).toBeVisible();
    expect(screen.queryByTestId("bell-dot")).toBeNull();
    expect(screen.queryByTestId("bell-badge")).toBeNull();
  });

  it("shows an accent dot - not a badge, not amber - for unread non-critical", () => {
    state.unreadCount = 4;
    render(<NotificationBell countryCode="CO" />);

    const dot = screen.getByTestId("bell-dot");
    expect(dot.className).toContain("bg-accent");
    expect(screen.queryByTestId("bell-badge")).toBeNull();
    expect(screen.getByRole("button", { name: "Notificaciones, 4 sin leer" })).toBeVisible();
  });

  it("shows a negative badge with the count only when something is critical", () => {
    state.unreadCount = 4;
    state.criticalUnread = 2;
    render(<NotificationBell countryCode="CO" />);

    const badge = screen.getByTestId("bell-badge");
    expect(badge).toHaveTextContent("2");
    expect(badge.className).toContain("bg-negative");
    expect(badge.className).not.toContain("warning");
    expect(screen.queryByTestId("bell-dot")).toBeNull();
  });

  it("never uses the warning colour anywhere in it", () => {
    state.unreadCount = 9;
    state.criticalUnread = 3;
    const { container } = render(<NotificationBell countryCode="CO" />);
    expect(container.querySelectorAll('[class*="warning"]')).toHaveLength(0);
  });

  it("opens the centre on click and closes it on Escape", async () => {
    render(<NotificationBell countryCode="CO" />);
    const button = screen.getByRole("button", { name: /Notificaciones/ });

    fireEvent.click(button);
    expect(await screen.findByRole("dialog", { name: "Notificaciones" })).toBeVisible();
    expect(button).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on a click outside the bell and the centre", async () => {
    render(
      <div>
        <p data-testid="outside">algo más en la página</p>
        <NotificationBell countryCode="CO" />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Notificaciones/ }));
    expect(await screen.findByRole("dialog")).toBeVisible();

    fireEvent.mouseDown(screen.getByTestId("outside"));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("reaches the thresholds sub-screen from the centre", async () => {
    render(<NotificationBell countryCode="CO" />);
    fireEvent.click(screen.getByRole("button", { name: /Notificaciones/ }));
    await screen.findByRole("dialog", { name: "Notificaciones" });

    fireEvent.click(screen.getByRole("button", { name: "Umbrales" }));
    expect(await screen.findByRole("dialog", { name: "Umbrales" })).toBeVisible();
  });
});
