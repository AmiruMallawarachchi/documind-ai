import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * vitest.config.ts — Component tests, in jsdom, with no Next.js runtime.
 *
 * Only the pieces that are pure rendering logic get tested here: how an answer's `[n]` markers become
 * buttons, what a status pill says, when a submit button is allowed to be clicked. Wiring that needs the
 * browser's own behaviour — SSE streaming, localStorage sessions, the Router — is verified by driving the
 * running app, because a jsdom stand-in for those would be testing the fake.
 *
 * `@` is aliased because tsconfig declares the same paths mapping and the components use it.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
    clearMocks: true,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
});
