#!/usr/bin/env node
/**
 * Compute the Chrome extension ID from the signing key.
 *
 * The extension ID is derived from the public key:
 *   SHA-256(DER-encoded public key) → first 16 bytes → 'a'-'p' hex encoding.
 *
 * Outputs the 32-character extension ID to stdout.
 */
import { createHash, createPublicKey } from "crypto";
import { readFileSync } from "fs";
import { resolve } from "path";

const keyPath = resolve(process.env.EXTENSION_KEY_PATH || "extension-key.pem");
const pem = readFileSync(keyPath, "utf8");
const pubKey = createPublicKey(pem);
const der = pubKey.export({ type: "spki", format: "der" });
const hash = createHash("sha256").update(der).digest();

const id = Array.from(hash.subarray(0, 16))
  .map((b) => String.fromCharCode(97 + (b >> 4)) + String.fromCharCode(97 + (b & 0xf)))
  .join("");

process.stdout.write(id);
