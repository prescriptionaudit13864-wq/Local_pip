import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import prescriptionBackend from './backend/backendPlugin.js'
import pilotAuth from './backend/pilotAuth.js'

// Local audit bridge (python -m audit_pipeline.app_server serve) -> /audit-api/audit
const auditProxy = {
  '/audit-api': {
    target: process.env.AUDIT_BRIDGE_URL || 'http://127.0.0.1:8765',
    changeOrigin: true,
    timeout: 300000,
    proxyTimeout: 300000,
    rewrite: (path) => path.replace(/^\/audit-api/, ''),
  },
}

// https://vite.dev/config/
export default defineConfig({
  // pilotAuth first: with PILOT_MODE=1 (pilot/start_pilot.ps1) every page and API call needs a doctor sign-in.
  plugins: [pilotAuth(), react(), prescriptionBackend()],
  server: { proxy: auditProxy },
  // The pilot serves the built app with `vite preview` behind a Cloudflare Tunnel link (*.trycloudflare.com).
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy: auditProxy, allowedHosts: ['.trycloudflare.com', '.ts.net'] },
})
