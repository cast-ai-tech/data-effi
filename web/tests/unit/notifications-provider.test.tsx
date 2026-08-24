import { act, cleanup, render, screen } from "@testing-library/react";
import { useContext, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BACKOFF_MIN_MS,
  CURSOR_STORAGE_KEY,
  NotificationsProvider,
  REVISION_DEBOUNCE_MS,
  RevisionContext,
  useNotifications,
} from "@/lib/notifications";
import type { EventsResponse, LiveEvent } from "@/lib/types";

/**
 * The long-poll, with the network replaced by a scripted `api.get`.
 *
 * Each test scripts what `/events` answers, drives the clock with fake timers,
 * and reads the provider's state through a probe. The contract under test is
 * the one the backend implements: the first call carries no `since` and only
 * returns a cursor; every later call carries the last cursor; events move the
 * counters and the revision; failures back off instead of hammering.
 */
const net = vi.hoisted(() => ({
  get: vi.fn<(path: string, options?: unknown) => Promise<unknown>>(),
  post: vi.fn<(path: string, body?: unknown) => Promise<unknown>>(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: net.get, post: net.post },
  };
});

function event(overrides: Partial<LiveEvent> & { type: string }): LiveEvent {
  return {
    id: 1,
    country_code: "CO",
    payload: {},
    created_at: "2026-08-23T12:00:00Z",
    ...overrides,
  };
}

/** Answers `/events` from a queue; anything else is an empty success. */
function scriptEvents(answers: Array<EventsResponse | Error>) {
  const queue = [...answers];
  net.get.mockImplementation(async (path: string) => {
    if (path.startsWith("/events")) {
      const next = queue.shift();
      if (next === undefined) return new Promise(() => {}); // hang: nothing more scripted
      if (next instanceof Error) throw next;
      return next;
    }
    if (path.startsWith("/notifications/unread-count")) {
      return { unread_count: 0, critical_unread_count: 0 };
    }
    return {};
  });
}

function eventCalls(): string[] {
  return net.get.mock.calls.map(([path]) => path).filter((path) => path.startsWith("/events"));
}

const seen = { revision: 0 };

function Probe() {
  const { cursor, unreadCount, criticalUnread, connected, setActive } = useNotifications();
  const revision = useContext(RevisionContext);
  seen.revision = revision;
  useEffect(() => {
    setActive(true);
    return () => setActive(false);
  }, [setActive]);
  return (
    <div>
      <span data-testid="cursor">{cursor ?? "null"}</span>
      <span data-testid="unread">{unreadCount}</span>
      <span data-testid="critical">{criticalUnread}</span>
      <span data-testid="connected">{String(connected)}</span>
      <span data-testid="revision">{revision}</span>
    </div>
  );
}

function mount() {
  return render(
    <NotificationsProvider>
      <Probe />
    </NotificationsProvider>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  net.get.mockReset();
  net.post.mockReset();
  window.sessionStorage.clear();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("NotificationsProvider - the loop", () => {
  it("asks for the cursor first, without `since`, then polls from it", async () => {
    scriptEvents([
      { cursor: 41, events: [] },
      { cursor: 41, events: [] },
    ]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(eventCalls()[0]).toBe("/events?wait=6");
    expect(eventCalls()[1]).toBe("/events?since=41&wait=6");
    expect(screen.getByTestId("cursor")).toHaveTextContent("41");
    expect(screen.getByTestId("connected")).toHaveTextContent("true");
    expect(window.sessionStorage.getItem(CURSOR_STORAGE_KEY)).toBe("41");
  });

  it("resumes from the cursor kept in sessionStorage after a reload", async () => {
    window.sessionStorage.setItem(CURSOR_STORAGE_KEY, "17");
    scriptEvents([{ cursor: 17, events: [] }]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(eventCalls()[0]).toBe("/events?since=17&wait=6");
  });

  it("counts a created notification, and a critical one twice over", async () => {
    scriptEvents([
      { cursor: 1, events: [] },
      {
        cursor: 3,
        events: [
          event({ id: 2, type: "notification.created", payload: { severity: "warning" } }),
          event({ id: 3, type: "notification.created", payload: { severity: "critical" } }),
        ],
      },
    ]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByTestId("unread")).toHaveTextContent("2");
    expect(screen.getByTestId("critical")).toHaveTextContent("1");
    expect(screen.getByTestId("cursor")).toHaveTextContent("3");
  });

  it("bumps the revision once for a burst of data events, after the debounce", async () => {
    scriptEvents([
      { cursor: 1, events: [] },
      {
        cursor: 4,
        events: [
          event({ id: 2, type: "batch.finished" }),
          event({ id: 3, type: "job_run.finished" }),
          event({ id: 4, type: "fx.refreshed" }),
        ],
      },
    ]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    // Before the debounce elapses nothing has moved.
    expect(screen.getByTestId("revision")).toHaveTextContent("0");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(REVISION_DEBOUNCE_MS);
    });
    expect(screen.getByTestId("revision")).toHaveTextContent("1");
  });

  it("ignores events on the very first answer: they predate the subscription", async () => {
    scriptEvents([
      { cursor: 9, events: [event({ id: 9, type: "notification.created" })] },
    ]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByTestId("unread")).toHaveTextContent("0");
  });

  it("backs off 2 s, then 4 s, when the API fails - and comes back", async () => {
    scriptEvents([
      new Error("asleep"),
      new Error("still asleep"),
      { cursor: 5, events: [] },
    ]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(eventCalls()).toHaveLength(1);
    expect(screen.getByTestId("connected")).toHaveTextContent("false");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKOFF_MIN_MS - 1);
    });
    expect(eventCalls()).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(eventCalls()).toHaveLength(2);

    // Second failure doubles the wait.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKOFF_MIN_MS * 2 - 1);
    });
    expect(eventCalls()).toHaveLength(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    // The third call succeeds and the loop goes straight into the next
    // long-poll (the fourth, left hanging by the script): no wait after success.
    expect(eventCalls().length).toBeGreaterThanOrEqual(3);
    expect(screen.getByTestId("connected")).toHaveTextContent("true");
  });

  it("does not poll while the tab is hidden, and resumes when it is shown", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    scriptEvents([{ cursor: 1, events: [] }]);
    mount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(eventCalls()).toHaveLength(0);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(eventCalls().length).toBeGreaterThan(0);
  });

  it("does nothing at all without a provider", () => {
    seen.revision = -1;
    render(<Probe />);
    expect(screen.getByTestId("unread")).toHaveTextContent("0");
    expect(screen.getByTestId("revision")).toHaveTextContent("0");
    expect(net.get).not.toHaveBeenCalled();
  });
});
