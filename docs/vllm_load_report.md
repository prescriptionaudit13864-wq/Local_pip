# Load test: doctors at the same time

Server: VLLMProvider unsloth/gemma-4-12B-it-qat-q4_0-unquantized. Each doctor sends 12 section checks (layer 1 then layer 2), team-written test cases, no patient data. Times are what one doctor waits.

| Doctors at once | Section checks per minute | Layer 1 median | Layer 1 95th pct | Layer 2 median | Layer 2 95th pct | Errors |
|---|---|---|---|---|---|---|
| 1 | 24.9 | 0.72 s | 1.98 s | 1.56 s | 1.89 s | 0 |
| 4 | 63.3 | 1.0 s | 4.28 s | 1.78 s | 4.1 s | 0 |
| 8 | 97.8 | 1.28 s | 5.43 s | 1.45 s | 4.81 s | 0 |
| 16 | 115.5 | 2.83 s | 8.64 s | 2.89 s | 9.05 s | 0 |
