import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_PORT = process.env.DEALBENCH_PORT ?? "8756";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The backend is the source of truth for /api in dev as well as in production,
    // so there is no second base-URL configuration to keep in sync.
    proxy: {
      "/api": { target: `http://127.0.0.1:${API_PORT}`, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
