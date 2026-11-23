# PrescribeX: live prescription audit for CMED (Sushastho) — concept note

**From:** PrescribeX team, HEAT project — [team lead name], [supervisor name, institution]
**Contact:** Tanim, [email], [phone]
**Date:** [date sent] · **Status:** draft for discussion, not an offer or a contract

## What PrescribeX is
PrescribeX checks a prescription **while the doctor writes it**. After each section is saved (complaints, history,
investigations, diagnosis, medicines, advice), it looks at that section in the context of everything written so far
and shows short alerts that name the entry and what is wrong, for example *"'Earache for 3 days': the side of the ear
is not specified"*. A final check runs on the full prescription when it is saved. It is built for Bangladesh primary
care: Bangla and English text, Bangladesh brand names and drug data, and Bangladesh guidelines (e.g. IMCI 2019).

## How it decides: two layers, one model
- **Layer 1, fast check:** Gemma 4 12B fine-tuned on physician-labelled Bangladesh prescriptions. It checks each
  section for 42 trained error types. Median time per section in our live tests: 1.8 s.
- **Layer 2, clinical reviewer:** the same model without the fine-tuning, acting as a senior general physician. It
  reviews entry by entry, then re-checks its own findings, and only the problem types it judges reliably are shown.
  Median 2.2 s. On 8 correct prescriptions in testing it raised no false alarms.
- **The model decides every alert.** Hand-written rules may only suggest what to look at; they never raise an alert.

On held-out tests (never used in training) layer 1 caught 92% of errors (95% CI 84–96%), including on medicines it
had never seen. On real prescriptions labelled by physicians it caught 79% (74–83%) and left 93% of correct sections
without a false alert. These are lab numbers; the pilot is how we measure real-world accuracy.

## Plug-and-play integration
PrescribeX is a small HTTP **audit API**: CMED's system sends the section the doctor just saved (as JSON) and gets
back the alerts to show. CMED keeps its own screens and workflow; we write the mapper from CMED's prescription format
to our API. If CMED's team is busy, we can supply a small widget so their page needs one line added. If the audit
server is down, CMED keeps working normally — the doctor just sees no alerts.

## Patient data stays local
The model runs on our own GPU server (or one at CMED); **no prescription is sent to an outside AI service**. We only
need de-identified prescription content; no names or phone numbers. Storage place, retention, who sees logs and
de-identification will be written into the data-sharing agreement, and the pilot will run only after BMRC ethics approval.

## Proposed pilot
- **25–50 CMED doctors**, soft launch with about a quarter of them, then all.
- Timeline: integration in CMED staging Jan–Feb 2027; pilot **14 March – 13 May 2027**.
- Measured weekly: use per doctor, alerts per prescription, alerts accepted or dismissed (with reason), speed, and
  accuracy from a physician's review of a sample.
- **Cost limit: at most 10 taka per prescription**, all-in for model hosting. We will report the measured cost at
  25 and 50 doctors before go-live, and run only in clinic hours if needed to stay under the limit.

## What we ask
1. A **meeting in the week of 29 November – 4 December 2026** (in person or online, 45 minutes).
2. At that meeting, if possible: your prescription data format, your drug master list, where your servers run (and
   whether a GPU is available), who signs for CMED, and how you release a new feature.
3. A mutual NDA (draft attached) so both sides can share technical details.

*Attached: NDA draft (`CMED_NDA_draft.md`).*
