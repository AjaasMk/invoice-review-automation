# Deployment Guide

The application is packaged as a single Docker service. It needs outbound HTTPS access to the selected model API and a writable `runs` directory for SQLite databases, uploaded invoices, and LangGraph checkpoints.

## Required environment variables

- `LLM_PROVIDER=deepseek`
- `DEEPSEEK_API_KEY=<your key>`
- `DEEPSEEK_MODEL=deepseek-flash`
- `DEMO_RESET_TOKEN=<strong private value>` to protect the public reset control
- `PORT=8000` when the hosting platform does not inject its own port

Gmail intake is optional. Add `GMAIL_IMAP_USER` and `GMAIL_IMAP_APP_PASSWORD` only when the deployed service should poll a dedicated demo inbox. Set `GMAIL_IMAP_MAILBOX=Invoice Review Queue` after creating that Gmail label and a filter that applies it only to expected invoice attachments; the app then avoids polling the whole inbox.

## Local container check

```bash
docker build -t invoice-decision-pipeline .
docker run --rm -p 8000:8000 --env-file .env -v invoice-runs:/app/runs invoice-decision-pipeline
```

Open `http://localhost:8000/api/health` first, then `http://localhost:8000`.

## Cloud host requirements

Use any Docker-capable host and configure:

- Health-check path: `/api/health`
- Container port: the platform-provided `PORT`, falling back to `8000`
- Persistent disk mounted at `/app/runs`
- One application instance for this demo, because SQLite is the persistence layer
- Model API credentials as secret environment variables, never build arguments
- `DEMO_RESET_TOKEN` as a secret. Enter it in the Settings reset dialog only when you need a fresh demo; reviewers cannot clear shared history without it.

After deployment, run one happy-path upload and one degraded-scan upload against the public URL before recording or submitting it. Do not seed the production demo database with prior runs unless those runs are intentionally part of the dashboard story.

The deployed application uses DeepSeek for extraction only. Validation, vendor/PO matching, duplicate checks, and approval decisions remain deterministic application logic.
