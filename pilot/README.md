# PrescribeX doctor pilot

Four doctors use PrescribeX from their own computers through one web link. Everything runs on this PC: the app,
saving the records, and both model layers on vLLM. Doctors need only a browser (Chrome or Edge) and their access code.

```
Doctor's browser ──HTTPS──► Tailscale Funnel link (https://<this-pc>.<tailnet>.ts.net)
                              │ encrypted end to end: Tailscale relays it but cannot read it
                              ▼
This PC:  app + doctor sign-in (port 4173) ──► audit server (8765) ──► vLLM in WSL2 (8000)
          records: prescribex-app/backend/data/doctor_<name>/...
```

Why not Vercel: a Vercel page would still have to call this PC for the model, and prescriptions would pass through
or be stored on a cloud service. With the Funnel link the records never leave this PC (project rule).

## One-time setup (Tanim, about 10 minutes)

1. **Sign in to Tailscale** on this PC: open *Tailscale* from the Start menu and sign in (a free personal account).
2. **Turn on Funnel**: the first time you start the pilot, Tailscale prints a link to approve Funnel for this PC.
   Open it, approve, and start again. (Admin page: *Access controls* → allow `funnel`; *DNS* → HTTPS certificates on.)
3. **Add the doctors**, one at a time:

   ```powershell
   .\pilot\add_doctor.ps1 -Name "Dr Rahman"
   ```

   It shows the doctor's access code once. Send it privately (a call or a private message), not in a group.
   Lost code: run it again (the old code stops working). Remove a doctor: add `-Remove`. See all: `-List`.
4. Copy `pilot\Start PrescribeX Pilot.bat` to the desktop if you like.

## Each pilot day

- **Start:** double-click `Start PrescribeX Pilot.bat` (or run `.\pilot\start_pilot.ps1`). It checks the GPU power cap
  and Tailscale, starts vLLM, the audit server and the app (three minimized windows: keep them open), opens the link,
  and prints it. The first start takes 3–5 minutes (vLLM loads the model).
- **Stop:** `.\pilot\stop_pilot.ps1`. It closes the public link first, then frees the GPU.
- The PC must stay on, awake and online while doctors work (the start script turns off sleep on mains power; the stop
  script turns it back on). Training or other GPU jobs cannot run during the pilot.

## What to send each doctor

> **PrescribeX pilot**
> 1. Open this link in Chrome or Edge: `https://….ts.net` (bookmark it).
> 2. Type your access code and press *Sign in*. You stay signed in for 12 hours.
> 3. Write the prescription as usual. Alerts appear beside each section within a second or two.
> 4. If the page says "Signed out", reload it and sign in again. If it does not open at all, call Tanim (the
>    pilot PC may be off).
> Your code is personal: please do not share it.

## Safety and privacy

- Every page, save and model call needs a signed-in doctor. Codes are 10 random characters, stored only as salted
  hashes (`pilot/doctors.json`); sessions are signed cookies (HttpOnly, Secure, 12 hours). 5 wrong codes from one
  address lock it for 15 minutes.
- Each doctor's records go to their own folder (`backend/data/doctor_<name>/`) and are marked with their name;
  in the Excel log their patient numbers are prefixed with the doctor id. Nothing is sent to a cloud service.
- `pilot/doctors.json`, `pilot/secret` and `pilot/link.txt` are never committed (`.gitignore`).
- Before real patients: the doctors' and patients' consent and any ethics approval the project needs are your
  responsibility; this setup does not replace them. The tool gives alerts to check, not decisions.
