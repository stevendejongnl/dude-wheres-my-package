#!/usr/bin/env node
/**
 * Pack the Chrome extension into a .crx file.
 *
 * Usage:
 *   node scripts/pack-extension.mjs <version>
 *
 * The signing key is read from $EXTENSION_KEY_PATH or extension-key.pem.
 * If the key doesn't exist, crx3 generates a new one (fine for dev,
 * but store a stable key as a GitHub secret for consistent extension IDs).
 */
import crx3 from "crx3";
import { resolve } from "path";

const version = process.argv[2];
if (!version) {
  console.error("Usage: node scripts/pack-extension.mjs <version>");
  process.exit(1);
}

const keyPath = resolve(process.env.EXTENSION_KEY_PATH || "extension-key.pem");
const crxPath = resolve(`dwmp-chrome-extension-${version}.crx`);
const extensionDir = resolve("chrome-extension");

console.log(`Packing extension v${version} → ${crxPath}`);
console.log(`Signing key: ${keyPath}`);

await crx3([extensionDir], { keyPath, crxPath });

console.log("Done.");
