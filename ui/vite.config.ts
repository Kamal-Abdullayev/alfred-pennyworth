import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: `npm run dev` serves the UI on :5173 and proxies /api to the FastAPI on :8787.
// Prod: `npm run build` → dist/, which api.py serves at /.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
