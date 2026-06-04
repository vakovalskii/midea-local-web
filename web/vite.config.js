import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Проксируем /api на FastAPI-бэкенд (server.py на :8000)
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5180,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
