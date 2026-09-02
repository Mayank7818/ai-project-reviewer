import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * Vite configuration.
 *
 * The `/api` proxy is the important part: in development the browser talks only
 * to http://localhost:5173, and Vite forwards `/api/*` to FastAPI on port 8000.
 * That means no cross-origin requests from the browser's point of view, and the
 * same relative URLs work in production behind a single reverse proxy.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
