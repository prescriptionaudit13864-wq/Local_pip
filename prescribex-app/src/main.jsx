import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App, { DOCTOR_PROFILE } from './App.jsx'

// Doctor pilot: show the signed-in doctor's own name and details (pilot/profile). Elsewhere /pilot/me does not
// exist and the built-in profile stays.
async function loadPilotProfile() {
  try {
    const res = await fetch('/pilot/me', { headers: { Accept: 'application/json' } })
    if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) return
    const me = await res.json()
    Object.assign(DOCTOR_PROFILE, { name: me.name || '', degrees: '', speciality: '', chamber: '', phone: '', regNo: '' },
      me.profile || {}, { pilot: true })
  } catch { /* not in the pilot */ }
}

loadPilotProfile().then(() => {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
