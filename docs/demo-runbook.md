# Five Minute Demo Runbook

## Before recording

- Start from a clean demo database and confirm `/api/health` returns `{"status":"ok"}`.
- Keep `happy_path.pdf`, `edge_a_split_1.pdf`, `edge_a_split_2.pdf`, and `edge_d_degraded.png` open in the file picker folder.
- Confirm `LLM_PROVIDER=nvidia` and the selected NVIDIA model without exposing the API key on screen. Use `moonshotai/kimi-k3` only after it passes the smoke suite; keep `meta/llama-3.2-11b-vision-instruct` ready as the verified fallback.
- Run the unit tests and golden evaluations once before recording.

## 0:00 to 0:35 Problem and design choice

Explain that AP teams receive inconsistent invoices and must match them to purchase orders. State the central design choice: Kimi extracts structured facts, while deterministic, inspectable rules make the payment decision.

## 0:35 to 1:45 Happy path

Upload `happy_path.pdf`. Show the intake preview and approve it. Move immediately to Live Run and point out each started/completed stage. Expand extraction, PO match, and decision. Finish on the dashboard row showing the vendor, invoice number, amount, and `AUTO_APPROVE` result.

## 1:45 to 3:05 Split billing edge case

Upload and approve `edge_a_split_1.pdf`, then `edge_a_split_2.pdf`. Explain that both reference the same PO. Show that the first invoice consumes 60% and the second is evaluated against the remaining 40%, rather than against the original PO total.

## 3:05 to 4:10 Degraded scan edge case

Upload `edge_d_degraded.png`. Show that uncertain fields remain null, confidence falls below the configured floor, and the result is `NEEDS_REVIEW`. Emphasize that the system refuses to invent financial data.

## 4:10 to 4:45 Auditability

Open a completed dashboard row and expand the persisted stages. Mention that each pure stage can be rerun after a rule change without paying for extraction again.

## 4:45 to 5:00 Close

Summarize the outcome: real PDF/image inputs, visible execution, deterministic decisions, deliberate edge cases, and a durable evidence trail. State the known scope boundaries: synthetic reference data, USD-only fixtures, and single-instance SQLite storage.

## Interview fallback

If the external model endpoint is unavailable, use `python preview_server.py` only to demonstrate the UI, clearly label it as a fallback, and show the previously recorded real-model smoke-test result. For the primary demo, always use `python -m web.server` or the deployed service.
