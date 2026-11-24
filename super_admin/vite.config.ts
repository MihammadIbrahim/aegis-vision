import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Core API runs separately; requests to /api are forwarded to it, so the
// browser talks to one origin and no CORS settings are needed. Override with VITE_API_TARGET.
declare const process: { env: Record<string, string | undefined> };
const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  // tokens.css is read as text by the contrast test, so Vitest must not stub it out.
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test/setup.ts"], css: { include: [/tokens\.css/] } },
});
