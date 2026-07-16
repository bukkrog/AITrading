import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base URL is read from VITE_API_BASE at build/runtime; defaults to the
// local FastAPI server. The dev server also proxies /api to it as a fallback.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
