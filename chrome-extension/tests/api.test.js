import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock chrome.storage.local and chrome.runtime before importing the module
const store = {};
globalThis.chrome = {
  storage: {
    local: {
      get: vi.fn(async (keys) => {
        if (Array.isArray(keys)) {
          return Object.fromEntries(keys.map((k) => [k, store[k]]));
        }
        if (typeof keys === "string") return { [keys]: store[keys] };
        return { ...store };
      }),
      set: vi.fn(async (items) => Object.assign(store, items)),
      remove: vi.fn(async (keys) => {
        for (const k of Array.isArray(keys) ? keys : [keys]) delete store[k];
      }),
    },
  },
  runtime: {
    getManifest: () => ({ version: "1.32.0" }),
  },
};

const {
  getConfig,
  saveConfig,
  clearConfig,
  isConfigured,
  healthCheck,
} = await import("../lib/api.js");

describe("storage helpers", () => {
  beforeEach(() => {
    Object.keys(store).forEach((k) => delete store[k]);
    vi.restoreAllMocks();
  });

  it("getConfig returns empty strings when not set", async () => {
    const config = await getConfig();
    expect(config.url).toBe("");
    expect(config.token).toBe("");
  });

  it("saveConfig stores url without trailing slash", async () => {
    await saveConfig("https://example.com/", "tok123");
    const config = await getConfig();
    expect(config.url).toBe("https://example.com");
    expect(config.token).toBe("tok123");
  });

  it("isConfigured returns false when unconfigured", async () => {
    expect(await isConfigured()).toBe(false);
  });

  it("isConfigured returns true when configured", async () => {
    await saveConfig("https://example.com", "tok");
    expect(await isConfigured()).toBe(true);
  });

  it("clearConfig removes all keys", async () => {
    await saveConfig("https://example.com", "tok");
    await clearConfig();
    expect(await isConfigured()).toBe(false);
  });
});

describe("apiCall error fallback", () => {
  beforeEach(async () => {
    Object.keys(store).forEach((k) => delete store[k]);
    vi.restoreAllMocks();
    await saveConfig("https://dwmp.test", "tok");
  });

  it("shows status code when server returns non-JSON error", async () => {
    const { browserPush } = await import("../lib/api.js");
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: "",
      json: async () => {
        throw new Error("not json");
      },
    });

    const result = await browserPush("<html>503</html>", "https://dpdgroup.com/nl");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("Server error (503)");
  });

  it("prefers detail from JSON response", async () => {
    const { browserPush } = await import("../lib/api.js");
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => ({ detail: "DPD is experiencing a technical issue" }),
    });

    const result = await browserPush("<html></html>", "https://dpdgroup.com/nl");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("DPD is experiencing a technical issue");
  });
});

describe("healthCheck", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns health data from server", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok", version: "1.32.0" }),
    });

    const result = await healthCheck("https://dwmp.test");
    expect(result).toEqual({ status: "ok", version: "1.32.0" });
    expect(globalThis.fetch).toHaveBeenCalledWith("https://dwmp.test/health");
  });

  it("throws on server error", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    await expect(healthCheck("https://dwmp.test")).rejects.toThrow("Server responded 500");
  });

  it("strips trailing slash from URL", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok" }),
    });

    await healthCheck("https://dwmp.test/");
    expect(globalThis.fetch).toHaveBeenCalledWith("https://dwmp.test/health");
  });
});
