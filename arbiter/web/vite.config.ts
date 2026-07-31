import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Pure SPA: React + Tailwind, no Next.js, no SSR, no server runtime.
// The console is a static bundle that talks to the FastAPI service over
// HTTP, which is the honest shape for this system -- every byte it renders
// comes from an authenticated API call, so there is nothing for a server
// framework to pre-render.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 3000,
    // Dev-only proxy so the browser and the API share an origin, which
    // sidesteps CORS entirely during development. Production serves the
    // built assets from a CDN/nginx and points VITE_API_BASE_URL at the
    // real gateway.
    proxy: {
      "/v1": { target: process.env.VITE_API_BASE_URL ?? "http://localhost:8000", changeOrigin: true },
      "/health": { target: process.env.VITE_API_BASE_URL ?? "http://localhost:8000", changeOrigin: true },
      "/ready": { target: process.env.VITE_API_BASE_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        // Split the router/vendor chunk so a UI change does not invalidate
        // the whole bundle for returning users.
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
