import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The FastAPI backend runs on :8000; proxying /api keeps the browser on a single origin.
// API_TARGET points a second dev server at another backend (e.g. a scratch copy of studies/).
export default defineConfig(({ mode }) => {
  const apiTarget = loadEnv(mode, ".", "").API_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": { target: apiTarget, changeOrigin: false },
      },
    },
  };
});
