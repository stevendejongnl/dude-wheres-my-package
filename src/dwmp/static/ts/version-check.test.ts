import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getPageVersion, isNewVersion } from "./version-check";

describe("getPageVersion", () => {
  afterEach(() => {
    document.body.textContent = "";
  });

  it("reads version from .version-badge element", () => {
    const badge = document.createElement("span");
    badge.className = "version-badge";
    badge.textContent = "v1.12.3";
    document.body.appendChild(badge);

    expect(getPageVersion()).toBe("v1.12.3");
  });

  it("returns null when badge is missing", () => {
    expect(getPageVersion()).toBeNull();
  });
});

describe("isNewVersion", () => {
  it("returns true when versions differ", () => {
    expect(isNewVersion("v1.12.2", "v1.12.3")).toBe(true);
  });

  it("returns false when versions match", () => {
    expect(isNewVersion("v1.12.3", "v1.12.3")).toBe(false);
  });

  it("returns false when page version is null", () => {
    expect(isNewVersion(null, "v1.12.3")).toBe(false);
  });

  it("returns false when server version is null (network error)", () => {
    expect(isNewVersion("v1.12.3", null)).toBe(false);
  });
});
