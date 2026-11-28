# PrescribeX doctor pilot

Four doctors use PrescribeX from their own computers through one web link. Everything runs on this PC: the app,
saving the records, and both model layers on vLLM. Doctors need only a browser (Chrome or Edge) and their access code.

```
Doctor's browser ──HTTPS──► Cloudflare Tunnel link (https://<four-words>.trycloudflare.com)
                              │ Cloudflare relays the traffic (it is decrypted at Cloudflare, then re-encrypted)
                              ▼
This PC:  app + doctor sign-in (port 4173) ──► audit server (8765) ──► vLLM in WSL2 (8000)
          records: prescribex-app/backend/data/doctor_<name>/...
```

**Privacy trade-off (accepted for the pilot, 24 Nov 2026):** Tailscale Funnel would keep traffic encrypted all the
way to this PC, but this PC's internet connection blocks Tailscale's servers. Cloudflare Tunnel works here, but
Cloudflare can technically read the prescriptions while relaying them. Nothing is stored on Cloudflare or Vercel;
the records are saved only on this PC. Get the project lead's OK for this before real patients, and switch back to
Tailscale (`tailscale funnel`) when the network allows it.

## One-time setup (Tanim, about 5 minutes)

1. **cloudflared** is installed (`winget install --id Cloudflare.cloudflared -e`). No Cloudflare account is needed.
2. **Add the doctors**, one at a time:

   ```powershell
   .\pilot\add_doctor.ps1 -Name "Dr Rahman"
   ```

   It shows the doctor's access code once. Send it privately (a call or a private message), not in a group.
   Lost code: run it again (the old code stops working). Remove a doctor: add `-Remove`. See all: `-List`.
3. Copy `pilot\Start PrescribeX Pilot.bat` to the desktop if you like.

## Each pilot day

- **Start:** double-click `Start PrescribeX Pilot.bat` (or run `.\pilot\start_pilot.ps1`). It checks the GPU power cap
  and cloudflared, starts vLLM, the audit server and the app (three minimized windows: keep them open), opens the link,
  and prints it. The first start takes 3–5 minutes (vLLM loads the model).
- **The link changes at every start** (free quick tunnel). Send the new link to the doctors each pilot day
  (it is also in `pilot\link.txt`). Their codes stay the same. A fixed link needs a Cloudflare account and a domain.
- **Stop:** `.\pilot\stop_pilot.ps1`. It closes the public link first, then frees the GPU.
- The PC must stay on, awake and online while doctors work (the start script turns off sleep on mains power; the stop
  script turns it back on). Training or other GPU jobs cannot run during the pilot.

## What to send each doctor

> **PrescribeX pilot**
> 1. Open today's link in Chrome or Edge: `https://….trycloudflare.com` (Tanim sends it each pilot day).
> 2. Type your access code and press *Sign in*. You stay signed in for 12 hours.
> 3. Write the prescription as usual. Alerts appear beside each section within a second or two.
> 4. If the page says "Signed out", reload it and sign in again. If it does not open at all, call Tanim (the
>    pilot PC may be off).
> Your code is personal: please do not share it.

## Safety and privacy

- Every page, save and model call needs a signed-in doctor. Codes are 10 random characters, stored only as salted
  hashes (`pilot/doctors.json`); sessions are signed cookies (HttpOnly, Secure, 12 hours). 5 wrong codes from one
  address lock it for 15 minutes.
- Each doctor's records go to their own folder (`backend/data/doctor_<id>_<Name>/`) and are marked with their name;
  in the Excel log their patient numbers are prefixed with the doctor id. Records are stored only on this PC.
  Inside: one folder per patient (`patient_<Patient ID>_<Name>/`, with `patient.json` = details and every change to
  them), one folder per prescription (`visit_01/`, ...) holding the latest version of each section and
  `history/<section>.json`: every save with what changed (before -> after), every AI check with each error and its
  suggested fix, the clinical reviewer's problems, and which errors a later edit resolved.
- `pilot/doctors.json`, `pilot/secret`, `pilot/link.txt` and the tunnel logs are never committed (`.gitignore`).
- Before real patients: the doctors' and patients' consent and any ethics approval the project needs are your
  responsibility; this setup does not replace them. The tool gives alerts to check, not decisions.
