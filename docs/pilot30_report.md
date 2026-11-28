# 30 doctors at once: tuned (FP8) vs previous (bf16) — pilot rehearsal 3 (28 Nov 2026)

**Question:** with 30 doctors working at the same time through the Cloudflare pilot link, how long does each doctor
wait per section and for the final check, and did the T02.2 tuning (`docs/vllm_tuning_report.md`) really improve it?

**Answer: yes.** On the identical workload the tuned server is about **3× faster per section** and finishes the
same work in **half the time**, with the **same errors caught** (124 of 130). The final check, pressed by all 30
doctors in the same second, returns for the median doctor after **30 s instead of 44 s**.

| | Previous (bf16, GPU share 0.88) | **Tuned (FP8, GPU share 0.95)** | Change |
|---|---|---|---|
| Wait per section, median | 12.2 s | **4.1 s** | −67% |
| Wait per section, 95% within | 24.5 s | **9.8 s** | −60% |
| Slowest single check | 125.7 s | **17.4 s** | −86% |
| Medicine section, median | 18.4 s | **6.0 s** | −67% |
| Checks over 10 s | 71% | **5%** | |
| Time for one doctor's 5 prescriptions (median) | 7.8 min | **4.1 min** | −47% |
| Waiting per prescription (sum of its checks) | 64 s | **22 s** | −65% |
| Final check, all 30 pressing at once: median / last | 44.3 / 57.1 s | **29.9 / 39.4 s** | −32% / −31% |
| Errors caught (sections) | 124 / 130 | 124 / 130 | same |
| Errors caught (final check) | 26 / 27 | 26 / 27 | same |
| Alerts on error-free lines | 65 | 93 | +28 (layer-2 reminders) |
| Energy for the workload | 46.3 Wh | **22.8 Wh** | −51% |
| Failed checks | 0 / 735 | 0 / 735 | |

## How the test was run

- **Path:** the real pilot path, through the public Cloudflare link `*.trycloudflare.com` → doctor sign-in → app
  (saves each section) → audit server → vLLM. The same link for both versions; only vLLM was restarted.
- **Doctors:** 30 test doctors, each with own access code and profile, started 1 s apart, all working at once.
- **Prescriptions:** 150, all different (`pilot_simulation.make_critical(150, seed=11)`), 5 per doctor: 38 clean,
  112 with 130 common errors (missing dose/frequency 30, missing duration 29, complaint without duration 17,
  duplicate drugs 13, antibiotic without a reason 12, missing referral 11, drug against history 7, interaction 5,
  wrong-sex complaint or test 6).
- **Each section:** save → layer 1 fast check → layer 2 clinical review, then 3–8 s before the next section. Real
  doctors take 1–3 minutes per section, so this is a **stress test**: about 30 checks in flight at once, where 30
  real doctors would produce about 3–8.
- **Final check:** afterwards, each doctor sent one completed prescription to the final "AI Audit" check, all 30
  released in the same second (worst case).
- **Same workload for both versions**, same code (branch at `25154e1`); 30 test doctors, their records and 870 Excel
  log rows removed afterwards.
- Scripts: `benchmark/pilot_simulation.py`, `benchmark/pilot_compare.py`, `benchmark/final_check_burst.py`;
  raw results: `benchmark/results/pilot30/`.

## Run timestamps

| | Previous | Tuned |
|---|---|---|
| Started | 11:52:58 | 11:47:11 |
| Last check finished | 12:01:33 | 11:51:54 |
| Total time | 8.6 min | 4.7 min |
| Section checks (failed) | 735 (0) | 735 (0) |
| Checks per minute | 84.5 | 152.0 |

## Wait per section check (both layers)

| | Previous | Tuned | Change |
|---|---|---|---|
| Median | 12.16 s | 4.06 s | −67% |
| 95% within | 24.51 s | 9.77 s | −60% |
| Slowest | 125.7 s | 17.4 s | −86% |
| Layer 1 median | 5.85 s | 1.61 s | −72% |
| Layer 1 95% | 16.05 s | 6.06 s | −62% |
| Layer 2 median | 5.37 s | 1.54 s | −71% |
| Layer 2 95% | 11.38 s | 5.35 s | −53% |
| Medicine section median | 18.37 s | 6.01 s | −67% |
| Medicine section 95% | 28.82 s | 12.93 s | −55% |

### Wait by time band

| Wait | Previous | Tuned |
|---|---|---|
| under 2 s | 16 (2%) | 110 (15%) |
| 2–4 s | 11 (1%) | 251 (34%) |
| 4–6 s | 17 (2%) | 210 (29%) |
| 6–10 s | 167 (23%) | 130 (18%) |
| over 10 s | 524 (71%) | 34 (5%) |

### By section (median / 95% within)

| Section | Previous | Tuned |
|---|---|---|
| chiefComplaints | 11.8 / 23.2 s | 3.8 / 9.3 s |
| pastHistory | 9.3 / 13.4 s | 1.9 / 3.5 s |
| investigations | 12.2 / 19.8 s | 4.1 / 8.2 s |
| diagnosis | 11.2 / 21.0 s | 3.8 / 7.4 s |
| rx | 18.4 / 28.8 s | 6.0 / 12.9 s |
| advice | 10.4 / 20.6 s | 2.7 / 6.9 s |

## Per doctor (5 prescriptions each)

| Doctor | Previous: first → last check | Previous: time to finish | Previous: median wait | Tuned: first → last check | Tuned: time to finish | Tuned: median wait |
|---|---|---|---|---|---|---|
| Sim Doctor 01 | 11:52:58 → 12:00:26 | 7.5 min | 12.1 s | 11:47:11 → 11:50:58 | 3.8 min | 3.6 s |
| Sim Doctor 02 | 11:52:58 → 12:00:29 | 7.5 min | 13.1 s | 11:47:11 → 11:51:09 | 4.0 min | 4.4 s |
| Sim Doctor 03 | 11:52:59 → 12:00:55 | 7.9 min | 13.3 s | 11:47:12 → 11:51:13 | 4.0 min | 4.1 s |
| Sim Doctor 04 | 11:53:00 → 12:01:04 | 8.1 min | 11.3 s | 11:47:13 → 11:51:26 | 4.2 min | 5.0 s |
| Sim Doctor 05 | 11:53:01 → 12:01:08 | 8.1 min | 14.1 s | 11:47:14 → 11:51:34 | 4.3 min | 5.1 s |
| Sim Doctor 06 | 11:53:02 → 12:00:36 | 7.6 min | 13.0 s | 11:47:15 → 11:51:24 | 4.2 min | 4.6 s |
| Sim Doctor 07 | 11:53:03 → 12:01:05 | 8.0 min | 12.3 s | 11:47:16 → 11:51:36 | 4.3 min | 4.6 s |
| Sim Doctor 08 | 11:53:04 → 12:01:02 | 8.0 min | 13.2 s | 11:47:17 → 11:51:27 | 4.2 min | 4.2 s |
| Sim Doctor 09 | 11:53:05 → 12:01:04 | 8.0 min | 11.4 s | 11:47:18 → 11:50:31 | 3.2 min | 3.5 s |
| Sim Doctor 10 | 11:53:06 → 12:01:03 | 7.9 min | 12.1 s | 11:47:19 → 11:51:34 | 4.3 min | 4.6 s |
| Sim Doctor 11 | 11:53:07 → 12:00:49 | 7.7 min | 12.0 s | 11:47:20 → 11:51:34 | 4.2 min | 4.0 s |
| Sim Doctor 12 | 11:53:08 → 12:00:28 | 7.3 min | 12.1 s | 11:47:21 → 11:51:14 | 3.9 min | 3.4 s |
| Sim Doctor 13 | 11:53:09 → 12:01:16 | 8.1 min | 12.9 s | 11:47:22 → 11:51:37 | 4.3 min | 5.1 s |
| Sim Doctor 14 | 11:53:10 → 12:01:00 | 7.8 min | 11.8 s | 11:47:23 → 11:51:36 | 4.2 min | 4.4 s |
| Sim Doctor 15 | 11:53:11 → 12:00:57 | 7.8 min | 12.3 s | 11:47:24 → 11:51:33 | 4.2 min | 3.7 s |
| Sim Doctor 16 | 11:53:12 → 12:00:33 | 7.3 min | 11.8 s | 11:47:25 → 11:51:16 | 3.9 min | 4.1 s |
| Sim Doctor 17 | 11:53:13 → 12:01:12 | 8.0 min | 11.6 s | 11:47:26 → 11:51:28 | 4.0 min | 2.8 s |
| Sim Doctor 18 | 11:53:14 → 12:00:55 | 7.7 min | 12.5 s | 11:47:27 → 11:51:22 | 3.9 min | 4.6 s |
| Sim Doctor 19 | 11:53:15 → 12:01:09 | 7.9 min | 13.1 s | 11:47:28 → 11:51:21 | 3.9 min | 4.1 s |
| Sim Doctor 20 | 11:53:16 → 12:00:05 | 6.8 min | 12.1 s | 11:47:29 → 11:50:42 | 3.2 min | 3.0 s |
| Sim Doctor 21 | 11:53:17 → 12:00:54 | 7.6 min | 12.0 s | 11:47:30 → 11:51:29 | 4.0 min | 3.2 s |
| Sim Doctor 22 | 11:53:18 → 12:01:33 | 8.3 min | 13.3 s | 11:47:31 → 11:51:54 | 4.4 min | 4.1 s |
| Sim Doctor 23 | 11:53:19 → 12:00:37 | 7.3 min | 11.6 s | 11:47:32 → 11:51:29 | 4.0 min | 4.1 s |
| Sim Doctor 24 | 11:53:20 → 12:01:15 | 7.9 min | 11.8 s | 11:47:33 → 11:51:53 | 4.3 min | 4.0 s |
| Sim Doctor 25 | 11:53:21 → 12:01:09 | 7.8 min | 12.0 s | 11:47:34 → 11:51:44 | 4.2 min | 4.6 s |
| Sim Doctor 26 | 11:53:22 → 12:01:12 | 7.8 min | 12.3 s | 11:47:35 → 11:51:40 | 4.1 min | 4.5 s |
| Sim Doctor 27 | 11:53:23 → 12:00:19 | 6.9 min | 12.4 s | 11:47:36 → 11:51:06 | 3.5 min | 3.9 s |
| Sim Doctor 28 | 11:53:24 → 12:01:01 | 7.6 min | 12.6 s | 11:47:37 → 11:51:37 | 4.0 min | 3.9 s |
| Sim Doctor 29 | 11:53:25 → 12:01:16 | 7.8 min | 12.0 s | 11:47:38 → 11:51:30 | 3.9 min | 3.2 s |
| Sim Doctor 30 | 11:53:26 → 12:01:07 | 7.7 min | 11.8 s | 11:47:39 → 11:51:45 | 4.1 min | 3.6 s |
| **Median doctor** | | **7.8 min** | | | **4.1 min** | |

## Per prescription

| | Previous | Tuned |
|---|---|---|
| Time waiting for checks (sum per prescription), median | 64.2 s (95%: 96.0 s) | 22.2 s (95%: 33.9 s) |
| Start to end of a prescription (incl. writing pauses), median | 87.8 s (95%: 121.8 s) | 43.3 s (95%: 59.3 s) |

## Checks finished per minute

| Minute | Previous | Tuned |
|---|---|---|
| 1 | 86 | 139 |
| 2 | 82 | 170 |
| 3 | 88 | 185 |
| 4 | 88 | 169 |
| 5 | 101 | 72 |
| 6 | 86 | 0 |
| 7 | 89 | 0 |
| 8 | 87 | 0 |
| 9 | 28 | 0 |

## Accuracy and answers

| | Previous | Tuned |
|---|---|---|
| Errors caught (strict) | 124 / 130 | 124 / 130 |
| Alerts on error-free lines | 65 | 93 |
| Layer 1 alerts same as Previous | — | 717 / 735 (97.6%) |
| Layer 2 alerts same as Previous | — | 700 / 735 (95.2%) |

### Errors caught by type

| Type | Previous | Tuned |
|---|---|---|
| Rx.freq.missing | 30 / 30 | 30 / 30 |
| Rx.duration.missing | 29 / 29 | 27 / 29 |
| CC.3 | 17 / 17 | 17 / 17 |
| Rx.dup | 10 / 13 | 11 / 13 |
| Rx.over | 12 / 12 | 12 / 12 |
| Adv.refer | 11 / 11 | 11 / 11 |
| Rx.hs | 6 / 7 | 6 / 7 |
| Rx.ddi | 5 / 5 | 5 / 5 |
| CC.demo | 3 / 3 | 3 / 3 |
| Inv.relevance | 1 / 3 | 2 / 3 |

## GPU and memory

| | Previous | Tuned |
|---|---|---|
| GPU busy (mean) | 94.8% | 91.7% |
| Power mean / peak | 319.5 / 366.11 W | 283.0 / 337.31 W |
| Energy | 46.3 Wh | 22.8 Wh |
| Prefix-cache reuse | 15.2% | 24.3% |
| Requests pushed out (preemptions) | 60 | 0 |


## Final check: all 30 doctors press "AI Audit" in the same second

The final check re-runs the fast check on every section of the finished prescription (one larger request per
doctor). Sent at the same instant by 30 signed-in doctors, each with a different prescription:

| | Previous | **Tuned** |
|---|---|---|
| Sent at | 12:05:32 | 12:03:32 |
| First result | 8.5 s | 9.3 s |
| Median doctor | 44.3 s | **29.9 s** |
| 95% of doctors | 54.4 s | **38.2 s** |
| Last doctor | 57.1 s | **39.4 s** |
| Failed | 0 | 0 |
| Errors caught | 26 / 27 | 26 / 27 |
| GPU busy / power mean / peak | 94% / 295 / 332 W | 86% / 241 / 304 W |

All 30 pressing in the same second is the worst case; in real use doctors finish at different times and a single
final check takes about 9 s. The audit server lets 16 checks run at once (`AUDIT_CONCURRENCY`, default 16 for vLLM),
so in this burst the last 14 doctors also wait for a free slot; raising it is a further tuning option.

## What a doctor experiences (tuned server)

| Moment | Under this stress test (30 writing non-stop) | Expected with 30 real doctors (sections minutes apart) |
|---|---|---|
| A section is saved → alerts shown | median 4 s, 95% within 10 s | about 1–3 s (close to the single-doctor 0.7–1.2 s per layer) |
| Medicine section | median 6 s, 95% within 13 s | about 2–5 s |
| Final check | median 30 s if all 30 press together | about 9–15 s |

Section checks run while the doctor writes the next section, so most of this wait is not idle time.

## False alerts

The tuned server raised 28 more alerts on error-free lines (93 vs 65), almost all from the clinical reviewer:
"severity not stated" 39 vs 28 and "side not stated" 34 vs 20. Layer-1 false alerts were about the same (20 vs 17).
As a result 25 of 38 clean prescriptions had no alert at all (previous: 33 of 38). This matches the 20-doctor tuning
test; if doctors find the reminders noisy, tune the reviewer's side/severity prompt.

## Conclusion

The T02.2 tuning is a real improvement under 30-doctor load: section checks 3× faster, slowest check 17 s instead of
126 s, a doctor's 5 prescriptions in 4.1 instead of 7.8 minutes, final check 30 s instead of 44 s in the worst case,
half the energy, and no change in errors caught. The cost is more layer-2 "side/severity" reminders.
