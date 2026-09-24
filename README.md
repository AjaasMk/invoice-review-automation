# Ledger - Invoice Review Automation

Ledger turns invoice files into clear, explainable review decisions.

1. Upload an invoice PDF, PNG, or JPG.
2. A person accepts it into the review queue.
3. DeepSeek reads the invoice fields.
4. Ledger checks the vendor, purchase order, amount, remaining PO balance, and duplicates.
5. The result is either **Auto approve**, **Needs review**, or **Reject**.

The AI only reads invoice data. The approval decision is made by fixed, visible rules, so every result can be explained.

## Live demo

The deployed demo is available at [invoice-review-automation-production.up.railway.app](https://invoice-review-automation-production.up.railway.app).

## Run locally

You need Python 3.11 or newer.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `.env` and add your DeepSeek key:

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-flash
```

Then start the app:

```powershell
python -m web.server
```

Open [http://localhost:8000](http://localhost:8000).

## Try the demo

The five prepared inputs are in `data/invoices/demo`.

| File | Expected result |
| --- | --- |
| `01_happy_path_scanned_acme.png` | Happy path: auto approve |
| `01_happy_path_scanned_globex_po_1002_phone_scan.png` | Happy path: auto approve |
| `03_zenith_vendor_not_found.pdf` | Needs review: add a vendor and PO, then re-check |
| `04_globex_amount_over_tolerance.pdf` | Needs review: hold for procurement |
| `05_low_confidence_degraded_scan.png` | Needs review: fields cannot be read safely |

To demonstrate a duplicate, upload either happy-path image once, let it complete, then upload the exact same image again.

## What each page does

- **Inbox**: receive files from upload, a watched folder, or Gmail.
- **Review Queue**: accept an incoming invoice before AI processing starts.
- **Live Run**: watch extraction, matching, and decision steps as they happen.
- **Invoices**: see every completed invoice and its reason.
- **Live database**: view the vendors and purchase orders used for matching.

## Gmail setup (optional)

Ledger can poll Gmail through IMAP. Create a Gmail label called `Invoice Review Queue`, have an employee apply it to invoice emails, then add these values to `.env`:

```env
GMAIL_IMAP_HOST=imap.gmail.com
GMAIL_IMAP_MAILBOX=Invoice Review Queue
GMAIL_IMAP_USER=your_gmail_address
GMAIL_IMAP_APP_PASSWORD=your_gmail_app_password
```

Use a Gmail **App Password**, not your normal Gmail password. Gmail setup is optional; manual upload works without it.

## Where the data lives

- Locally: `runs/app.db`
- On Railway: `/app/runs/app.db` on a persistent Railway Volume

The database stores invoices, decisions, vendors, purchase orders, and the audit trail. It survives a normal Railway redeploy.

## Deploy on Railway

1. Create a Railway service from this GitHub repository.
2. Add a volume mounted at `/app/runs`.
3. Set the health check path to `/api/health`.
4. Add `DEEPSEEK_API_KEY`, `LLM_PROVIDER=deepseek`, and `DEEPSEEK_MODEL=deepseek-flash` as Railway variables.
5. Set `RAILWAY_RUN_UID=0` for the SQLite volume permission.
6. Keep one replica because this demo uses SQLite.
7. Generate a public domain.

For a hosted demo reset, set a private `DEMO_RESET_TOKEN` Railway variable. Enter the same token in the app's Settings dialog before resetting. Leave it blank locally if you want the local reset button to work without a token.

## Notes

- The included vendors and POs are sample data for the take-home assessment.
- This is a single-service demo using SQLite. A larger, multi-user production system would use Postgres.
- Exact duplicate files are detected before an extra AI call is made.
