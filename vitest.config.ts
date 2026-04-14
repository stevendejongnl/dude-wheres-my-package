import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: [
      "src/dwmp/static/ts/**/*.test.ts",
      "chrome-extension/tests/**/*.test.js",
    ],
  },
});
