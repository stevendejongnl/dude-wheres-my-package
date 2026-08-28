import { describe, it, expect, vi, afterEach } from "vitest";
import { subscribeToPush, urlBase64ToUint8Array } from "./notifications";

describe("urlBase64ToUint8Array", () => {
  it("decodes a standard base64url VAPID key into bytes", () => {
    // "test" base64-encoded is "dGVzdA==" -> urlsafe no-padding: "dGVzdA"
    const result = urlBase64ToUint8Array("dGVzdA");
    expect(Array.from(result)).toEqual([116, 101, 115, 116]); // "test"
  });

  it("handles URL-safe characters (-, _)", () => {
    // bytes [0xfb, 0xff] -> base64 "+/8=" -> urlsafe "-_8"
    const result = urlBase64ToUint8Array("-_8");
    expect(Array.from(result)).toEqual([0xfb, 0xff]);
  });
});

describe("subscribeToPush", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("does nothing when a subscription already exists", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue({ endpoint: "existing" }),
        subscribe: vi.fn(),
      },
    } as unknown as ServiceWorkerRegistration;

    await subscribeToPush(registration);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
  });

  it("subscribes and posts the subscription when none exists", async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ publicKey: "dGVzdA" }) })
      .mockResolvedValueOnce({ ok: true });
    vi.stubGlobal("fetch", fetchSpy);

    const subscription = {
      toJSON: () => ({
        endpoint: "https://push.example.com/1",
        keys: { p256dh: "p256dh-key", auth: "auth-key" },
      }),
    };
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe: vi.fn().mockResolvedValue(subscription),
      },
    } as unknown as ServiceWorkerRegistration;

    await subscribeToPush(registration);

    expect(registration.pushManager.subscribe).toHaveBeenCalledWith(
      expect.objectContaining({ userVisibleOnly: true }),
    );
    expect(fetchSpy).toHaveBeenLastCalledWith(
      "/api/v1/push/subscribe",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          endpoint: "https://push.example.com/1",
          p256dh: "p256dh-key",
          auth: "auth-key",
        }),
      }),
    );
  });

  it("does not throw when the vapid-public-key fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    const registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe: vi.fn(),
      },
    } as unknown as ServiceWorkerRegistration;

    await expect(subscribeToPush(registration)).resolves.toBeUndefined();
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
  });
});
