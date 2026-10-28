import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";


const target = process.env.GRAFT_API ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/query": {
        target,
        changeOrigin: true,
      },
      "/api": {
        target,
        changeOrigin: true,
      },
      "/health": {
        target,
        changeOrigin: true,
      },
      "/index": {
        target,
        changeOrigin: true,
      },
    },
  },
});
