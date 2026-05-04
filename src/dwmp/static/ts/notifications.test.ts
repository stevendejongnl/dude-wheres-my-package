import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  buildPayload,
  parseBadgeCount,
  requestPermission,
  initNotifications,
} from "./notifications";

// --- buildPayload ---

describe("buildPayload", () => {
  it("returns null when count did not increase", () => {
    expect(buildPayload(3, 3)).toBeNull();
    expect(buildPayload(2, 5)).toBeNull();
  });

  it("returns null when old count is negative", () => {
    expect(buildPayload(3, -1)).toBeNull();
  });

  it("returns generic message for +1 without badge metadata", () => {
    const payload = buildPayload(1, 0);
    expect(payload).not.toBeNull();
    expect(payload!.body).toBe("A package status has changed");
    expect(payload!.title).toBe("Dude, Where's My Package?");
    expect(payload!.icon).toBe("/static/icon-64.png");
    expect(payload!.tag).toBe("dwmp-update");
  });

  it("returns rich message for +1 with badge metadata", () => {
    const badge = document.createElement("span");
    badge.dataset.count = "1";
    badge.dataset.carrier = "dhl";
    badge.dataset.tracking = "CQ964395186DE";
    badge.dataset.newStatus = "In Transit";
    badge.dataset.description = "The shipment has been loaded onto the delivery vehicle";
    badge.dataset.label = "";

    const payload = buildPayload(1, 0, badge);
    expect(payload).not.toBeNull();
    expect(payload!.body).toContain("DHL");
    expect(payload!.body).toContain("In Transit");
    expect(payload!.body).toContain("loaded onto the delivery vehicle");
  });

  it("uses label over tracking number when available", () => {
    const badge = document.createElement("span");
    badge.dataset.count = "1";
    badge.dataset.carrier = "postnl";
    badge.dataset.tracking = "3STEST123";
    badge.dataset.newStatus = "Delivered";
    badge.dataset.label = "New headphones";

    const payload = buildPayload(1, 0, badge);
    expect(payload!.body).toContain("New headphones");
    expect(payload!.body).not.toContain("3STEST123");
  });

  it("prefixes icon URL when <meta name=\"dwmp-base\"> is set (HA ingress)", () => {
    const meta = document.createElement("meta");
    meta.name = "dwmp-base";
    meta.content = "/api/hassio_ingress/xyz";
    document.head.appendChild(meta);

    try {
      const payload = buildPayload(1, 0);
      expect(payload!.icon).toBe("/api/hassio_ingress/xyz/static/icon-64.png");
    } finally {
      meta.remove();
    }
  });

  it("returns plural message for +2 or more", () => {
    const payload = buildPayload(5, 2);
    expect(payload).not.toBeNull();
    expect(payload!.body).toBe("3 package updates");
  });

  it("returns correct diff from 0 to large number", () => {
    const payload = buildPayload(99, 0);
    expect(payload!.body).toBe("99 package updates");
  });
});

// --- parseBadgeCount ---

describe("parseBadgeCount", () => {
  function makeContainer(dataCount: string | null): HTMLElement {
    const container = document.createElement("div");
    if (dataCount !== null) {
      const span = document.createElement("span");
      span.dataset.count = dataCount;
      span.textContent = dataCount;
      container.appendChild(span);
    }
    return container;
  }

  it("reads data-count from a child element", () => {
    expect(parseBadgeCount(makeContainer("5"))).toBe(5);
  });

  it("returns 0 when no data-count element exists", () => {
    expect(parseBadgeCount(makeContainer(null))).toBe(0);
  });

  it("returns 0 for empty container", () => {
    const container = document.createElement("div");
    expect(parseBadgeCount(container)).toBe(0);
  });

  it("returns 0 for non-numeric data-count", () => {
    expect(parseBadgeCount(makeContainer("abc"))).toBe(0);
  });

  it("returns 0 for data-count='0'", () => {
    expect(parseBadgeCount(makeContainer("0"))).toBe(0);
  });
});

// --- requestPermission ---

describe("requestPermission", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("calls Notification.requestPermission after delay when default", () => {
    const mockRequest = vi.fn().mockResolvedValue("granted");
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: mockRequest,
    });

    requestPermission(1000);

    expect(mockRequest).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(mockRequest).toHaveBeenCalledOnce();
  });

  it("does not call requestPermission when already granted", () => {
    const mockRequest = vi.fn();
    vi.stubGlobal("Notification", {
      permission: "granted",
      requestPermission: mockRequest,
    });

    requestPermission(0);
    vi.advanceTimersByTime(1000);
    expect(mockRequest).not.toHaveBeenCalled();
  });

  it("does not call requestPermission when denied", () => {
    const mockRequest = vi.fn();
    vi.stubGlobal("Notification", {
      permission: "denied",
      requestPermission: mockRequest,
    });

    requestPermission(0);
    vi.advanceTimersByTime(1000);
    expect(mockRequest).not.toHaveBeenCalled();
  });
});

// --- initNotifications (integration) ---

describe("initNotifications", () => {
  let notifSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    notifSpy = vi.fn();
    vi.stubGlobal("Notification", Object.assign(notifSpy, {
      permission: "granted",
      requestPermission: vi.fn(),
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function fireSwap(targetId: string, dataCount: string): void {
    const target = document.createElement("div");
    target.id = targetId;
    const span = document.createElement("span");
    span.dataset.count = dataCount;
    span.textContent = dataCount;
    target.appendChild(span);

    const evt = new CustomEvent("htmx:afterSwap", {
      detail: { target },
    });
    document.dispatchEvent(evt);
  }

  it("fires a notification when unread count increases", () => {
    const cleanup = initNotifications();

    fireSwap("notif-badge", "3");

    expect(notifSpy).toHaveBeenCalledOnce();
    expect(notifSpy).toHaveBeenCalledWith(
      "Dude, Where's My Package?",
      expect.objectContaining({ body: "3 package updates" }),
    );

    cleanup();
  });

  it("does not fire when count stays the same", () => {
    const cleanup = initNotifications();

    fireSwap("notif-badge", "2");
    notifSpy.mockClear();

    fireSwap("notif-badge", "2");
    expect(notifSpy).not.toHaveBeenCalled();

    cleanup();
  });

  it("does not fire when count decreases", () => {
    const cleanup = initNotifications();

    fireSwap("notif-badge", "5");
    notifSpy.mockClear();

    fireSwap("notif-badge", "3");
    expect(notifSpy).not.toHaveBeenCalled();

    cleanup();
  });

  it("ignores htmx swaps on other targets", () => {
    const cleanup = initNotifications();

    fireSwap("some-other-element", "10");
    expect(notifSpy).not.toHaveBeenCalled();

    cleanup();
  });

  it("cleanup removes the listener", () => {
    const cleanup = initNotifications();
    cleanup();

    fireSwap("notif-badge", "5");
    expect(notifSpy).not.toHaveBeenCalled();
  });

  it("does not re-fire on PWA reopen when count unchanged", () => {
    // Simulate first session: count goes to 2, saves to localStorage
    const cleanup1 = initNotifications();
    fireSwap("notif-badge", "2");
    expect(notifSpy).toHaveBeenCalledOnce();
    cleanup1();

    // Simulate reopen: new initNotifications() reads stored count=2
    notifSpy.mockClear();
    const cleanup2 = initNotifications();
    fireSwap("notif-badge", "2");
    expect(notifSpy).not.toHaveBeenCalled();
    cleanup2();
  });

  it("fires notification for count going from 0 to 1", () => {
    const cleanup = initNotifications();

    fireSwap("notif-badge", "1");

    expect(notifSpy).toHaveBeenCalledOnce();
    // Without data-carrier on the badge, falls back to generic message
    expect(notifSpy).toHaveBeenCalledWith(
      "Dude, Where's My Package?",
      expect.objectContaining({ body: "A package status has changed" }),
    );

    cleanup();
  });
});
