import { describe, expect, it } from "vitest";
import {
  CARRIER_LOGIN_PATTERNS,
  detectCarrier,
  isNewerVersion,
} from "../lib/carriers.js";

describe("detectCarrier", () => {
  it("detects Amazon NL", () => {
    expect(detectCarrier("https://www.amazon.nl/gp/your-account/order-history")).toBe("amazon");
  });

  it("detects Amazon DE", () => {
    expect(detectCarrier("https://www.amazon.de/gp/css/order-history")).toBe("amazon");
  });

  it("detects Amazon COM", () => {
    expect(detectCarrier("https://www.amazon.com/gp/your-account/order-history")).toBe("amazon");
  });

  it("detects PostNL", () => {
    expect(detectCarrier("https://jouw.postnl.nl/track-and-trace")).toBe("postnl");
  });

  it("detects DHL ecommerce", () => {
    expect(detectCarrier("https://my.dhlecommerce.nl/home")).toBe("dhl");
  });

  it("detects DHL NL", () => {
    expect(detectCarrier("https://www.dhl.nl/tracking")).toBe("dhl");
  });

  it("detects DPD", () => {
    expect(detectCarrier("https://www.dpdgroup.com/nl/mydpd/my-parcels")).toBe("dpd");
  });

  it("detects subdomain matches", () => {
    expect(detectCarrier("https://some.sub.amazon.nl/page")).toBe("amazon");
  });

  it("returns null for unknown domains", () => {
    expect(detectCarrier("https://www.google.com")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(detectCarrier("")).toBeNull();
  });

  it("returns null for malformed URL", () => {
    expect(detectCarrier("not-a-url")).toBeNull();
  });
});

describe("CARRIER_LOGIN_PATTERNS (amazon)", () => {
  const matches = (url) =>
    CARRIER_LOGIN_PATTERNS.amazon.some((p) => url.toLowerCase().includes(p));

  it("matches the legacy /ap/signin page", () => {
    expect(matches("https://www.amazon.nl/ap/signin?openid.return_to=foo")).toBe(true);
  });

  it("matches MFA challenges at /ap/mfa", () => {
    expect(matches("https://www.amazon.nl/ap/mfa?arb=abc")).toBe(true);
  });

  it("matches the new /ax/claim flow (logged-out redirect target)", () => {
    expect(matches("https://www.amazon.nl/ax/claim?arb=abc")).toBe(true);
    expect(matches("https://www.amazon.nl/ax/claim/intent?arb=abc")).toBe(true);
  });

  it("does not match the post-login orders page", () => {
    expect(matches("https://www.amazon.nl/your-orders/orders")).toBe(false);
  });
});

describe("isNewerVersion", () => {
  it("detects major version bump", () => {
    expect(isNewerVersion("2.0.0", "1.32.0")).toBe(true);
  });

  it("detects minor version bump", () => {
    expect(isNewerVersion("1.33.0", "1.32.0")).toBe(true);
  });

  it("detects patch version bump", () => {
    expect(isNewerVersion("1.32.1", "1.32.0")).toBe(true);
  });

  it("returns false for same version", () => {
    expect(isNewerVersion("1.32.0", "1.32.0")).toBe(false);
  });

  it("returns false for older version", () => {
    expect(isNewerVersion("1.31.0", "1.32.0")).toBe(false);
  });

  it("handles missing patch components", () => {
    expect(isNewerVersion("1.33", "1.32.0")).toBe(true);
  });
});
