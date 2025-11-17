# Alfred e-Disclosure Workflow

Python helpers and an Alfred workflow for browsing and opening Russian e-disclosure (e-disclosure.ru) financial reports. The project exposes the same logic as a CLI and as Alfred keywords so you can quickly list RSBU or MSFO filings, filter by period, and open cached PDFs with one keystroke.

## Features
- Maps short tickers to the portal company IDs stored in `tickers.csv`.
- Normalizes verbose period names (`I квартал 2024` → `2024Q1`, `2024, 9 месяцев` → `2024M9`).
- Shows publish dates and document type badges (`МСФО` / `РСБУ`) sorted by newest first.
- Caches downloaded ZIP archives under `~/tmp/alfred-e-disclosure/<TICKER>` so the same PDF opens instantly next time.
- Falls back to stdlib networking but can impersonate Chrome when `curl_cffi` is available.

## Repository Layout
- `list_reports.py` – Alfred Script Filter / CLI that prints report candidates as JSON.
- `open_report.py` – Downloader/opener CLI used by Alfred when an item is selected.
- `tickers.csv` – Ticker → e-disclosure company ID mapping; extend it as needed.
- `info.plist` – Workflow definition consumed by Alfred.
- `build_workflow.sh` – Packs the workflow into `dist/alfred-e-disclosure.alfredworkflow`.

## Requirements
- macOS with Alfred 5 (Powerpack) for workflow usage.
- Python 3.10+ (the scripts rely only on stdlib by default).

## Development Setup
For better TLS fingerprinting (recommended), install the optional dependency:

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Build the workflow bundle with dependencies included
./build_workflow.sh
```

The workflow automatically uses `curl_cffi` when available, falling back to stdlib networking otherwise.

## Environment Variables
- `EDISCLOSURE_COOKIE` – paste cookies from an authenticated browser session if the site serves CAPTCHA pages.
- `EDISCLOSURE_IMPERSONATE` – override the curl_cffi impersonation preset (defaults to `chrome124`).

## CLI Usage
List RSBU or MSFO reports directly in the terminal:
```bash
python3 list_reports.py msfo STSB         # list all MSFO reports for STSB
python3 list_reports.py rsbu MOEX 2024    # only RSBU reports whose period starts with 2024
python3 list_reports.py msfo MRKP --alfred-query "MRKP 2023Q4"
```

The script prints Alfred-ready JSON; pipe it through `jq` for readability.

Open a report (uses the JSON emitted by `list_reports.py`):
```bash
python3 open_report.py \
  --ticker STSB \
  --period 2024Q1 \
  --doc-type МСФО \
  --publish-date 2024-05-15 \
  --url https://www.e-disclosure.ru/.../report.zip
```

When invoked from Alfred you normally pass the entire payload:
```bash
python3 open_report.py --payload '{"ticker":"STSB","url":"...","period":"2024Q1","doc_type":"МСФО","publish_date":"2024-05-15"}'
```

Both scripts honor the environment variables above and reuse cached ZIP/PDF files.

## Alfred Workflow
1. Import `dist/alfred-e-disclosure.alfredworkflow` into Alfred.
2. Use the `msfo` keyword for IFRS reports or `rsbu` for Russian GAAP.
3. Type `TICKER [PERIOD_PREFIX]`, e.g. `STSB 2024` or `MOEX 2023Q4`.
4. Press Enter on an item to download (if needed), extract, cache, and open the PDF.
5. Hold `⌘` (Cmd) while pressing Enter to download/extract and copy the PDF to `~/Downloads` without opening it.

## Build the Bundle
```bash
chmod +x build_workflow.sh
./build_workflow.sh
```
The script copies the Python sources, `tickers.csv`, and `info.plist` into `dist/workflow/` and zips everything into `dist/alfred-e-disclosure.alfredworkflow`.

## Testing Tips
- Run `python3 list_reports.py msfo STSB | jq` to confirm listing works.
- Select any emitted item, copy its JSON payload, and feed it to `open_report.py --payload ...` to ensure downloads and caching succeed.
- If e-disclosure responds with bot protection, grab cookies from your browser’s dev tools and set `EDISCLOSURE_COOKIE` before re-running the scripts.

