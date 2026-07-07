import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发服务器把 /api 代理到后端 :8766，便于本地连真实后端（含 interaction.v1 端点就绪后）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8766",
        changeOrigin: true,
      },
    },
  },
});
