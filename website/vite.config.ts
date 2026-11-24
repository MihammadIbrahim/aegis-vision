import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Core API runs separately; requests to /api are forwarded to it, so the
// browser talks to one origin and no CORS settings are needed. Override with VITE_API_TARGET.
declare const process: { env: Record<string, string | undefined> };
const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // ws: true also forwards WebSocket upgrades, so /api/v1/realtime/ws works through the same origin (decision D-026).
  server: { proxy: { "/api": { target: apiTarget, changeOrigin: false, ws: true } } },
  // Third-party code (React, the router, icons) changes less often than the app: a separate chunk keeps
  // both under the 500 kB warning and lets browsers cache the libraries across deployments. The LiveKit voice SDK
  // (decision D-026) gets its own chunk, loaded only when a call connects (dynamic import in calls/voice.ts).
  build: {
    // The only chunk above 500 kB is the lazily loaded LiveKit SDK ("voice", ~580 kB, ~150 kB gzipped).
    chunkSizeWarningLimit: 600,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "voice",
              priority: 2,
              test: /node_modules[\\/](livekit-client|@livekit|@bufbuild|sdp-transform|sdp|webrtc-adapter|loglevel|machina|jose|typed-emitter)[\\/]/,
            },
            { name: "vendor", priority: 1, test: /node_modules/ },
          ],
        },
      },
    },
  },
  // tokens.css is read as text by the contrast test, so Vitest must not stub it out.
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test/setup.ts"], css: { include: [/tokens\.css/] } },
});
