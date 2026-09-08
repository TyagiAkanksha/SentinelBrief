import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "node",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Fixed non-UTC zone (+05:30, no DST): a UTC test runner can't tell naive-as-local
    // apart from naive-as-UTC, so this makes any test that silently depends on the host
    // zone (e.g. formatDatetimeLocalUtc's naive-as-UTC clause) fail everywhere, CI included.
    env: { TZ: "Asia/Kolkata" },
  },
});
