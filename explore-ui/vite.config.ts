import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(import.meta.dirname, "./src"),
    },
  },
  // MapLibre locates its web worker relative to its own file, so it must not be
  // pre-bundled into .vite/deps.
  optimizeDeps: { exclude: ["maplibre-gl"] },
  server: {
    // The notebook-factory API sends no CORS headers, so in development we
    // proxy /api to staging and the browser sees a same-origin request.
    proxy: {
      "/api": {
        target: "https://notebook-factory-staging.montandondata.org",
        changeOrigin: true,
      },
    },
  },
})
