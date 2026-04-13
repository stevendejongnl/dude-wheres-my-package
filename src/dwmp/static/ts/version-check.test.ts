import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getBasePath, getPageVersion, isNewVersion } from "./version-check";

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

describe("getBasePath", () => {
  afterEach(() => {
    document.head.querySelectorAll('meta[name="dwmp-base"]').forEach((m) => m.remove());
  });

  it("reads prefix from <meta name=\"dwmp-base\">", () => {
    const meta = document.createElement("meta");
    meta.name = "dwmp-base";
    meta.content = "/api/hassio_ingress/abc";
    document.head.appendChild(meta);

    expect(getBasePath()).toBe("/api/hassio_ingress/abc");
  });

  it("returns empty string when meta is missing (k8s/direct-port deployment)", () => {
    expect(getBasePath()).toBe("");
  });

  it("returns empty string when meta content is empty", () => {
    const meta = document.createElement("meta");
    meta.name = "dwmp-base";
    meta.content = "";
    document.head.appendChild(meta);

    expect(getBasePath()).toBe("");
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
