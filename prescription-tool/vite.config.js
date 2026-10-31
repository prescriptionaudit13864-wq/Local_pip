import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import prescriptionBackend from './backend/backendPlugin.js'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), prescriptionBackend()],
  server: {
    // Local audit bridge (python -m v3.app_server serve) -> /audit-api/audit
    proxy: {
      '/audit-api': {
        target: process.env.AUDIT_BRIDGE_URL || 'http://127.0.0.1:8765',
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        rewrite: (path) => path.replace(/^\/audit-api/, ''),
      },
    },
  },
})
