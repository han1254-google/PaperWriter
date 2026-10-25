import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 预构建静态页：运行时由 FastAPI 托管 dist/，同源访问 /api
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
  },
});
