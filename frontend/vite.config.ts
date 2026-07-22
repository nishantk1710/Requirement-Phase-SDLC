/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// base: "./" so the built assets resolve when FastAPI serves dist/ at "/".
// Dev proxy: forward /api to the FastAPI app so `npm run dev` works cross-origin-free.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "node",           // the client tests mock fetch — no DOM needed
    include: ["src/**/*.test.ts"],
  },
});
