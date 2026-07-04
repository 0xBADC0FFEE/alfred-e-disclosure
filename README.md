# Alfred e-Disclosure Workflow

An Alfred workflow for browsing and opening Russian e-disclosure
(e-disclosure.ru) financial reports. As of `2.0.0` it is a **thin wrapper** around
the globally installed [`edisclosure`](https://github.com/0xBADC0FFEE/edisclosure)
CLI: the workflow does no fetching, anti-bot handling, caching, or extraction of
its own — it shells out to the CLI and reshapes its JSON into Alfred rows.

## Requirements

- macOS with Alfred 5 (Powerpack).
- System `python3` (3.10+). The wrapper is **stdlib only** — no venv, no deps.
- The `edisclosure` CLI on your `PATH`:

  ```bash
  uv tool install edisclosure    # drops a shim on ~/.local/bin
  ```

  The workflow finds the CLI even under Alfred's truncated `PATH` (falls back to
  `~/.local/bin/edisclosure`) and shows a clear "CLI не установлен" row if it is
  missing.

## Usage

1. Import `dist/alfred-e-disclosure.alfredworkflow` into Alfred.
2. Type a keyword and a ticker:
   - `msfo TICKER [PERIOD]` — IFRS reports
   - `rsbu TICKER [PERIOD]` — Russian GAAP reports
   - `annual TICKER [PERIOD]` — annual reports
3. A bare ticker prefix autocompletes matching companies; add a space (or a
   period) to list reports, e.g. `msfo LKOH` or `rsbu MOEX 2024`.
4. `↵` downloads (if needed), extracts, and opens the PDF.
5. `⌘↵` saves the PDF to `~/Downloads` without opening it.
6. `⌥↵` forces a cache refresh (the subtitle shows the current cache age).
7. If the portal shows a check, a "Портал заблокировал запрос" row appears —
   `↵` opens a real browser window to solve it once (`edisclosure arm`); the
   armed session is shared with the CLI's other consumers.

Listing is non-blocking: it serves cache-or-stale instantly and refreshes in a
detached CLI worker, re-polling via Alfred's `rerun` until fresh data lands. The
type-as-you-search path never opens a browser window on its own.

## Environment

All fetch/anti-bot configuration
(`EDISCLOSURE_COOKIE` / `IMPERSONATE` / `ARM_TIMEOUT` / `CACHE_DIR`) is read by the
CLI itself — the wrapper does not forward it. Set `EDISCLOSURE_DEBUG=1` to log the
wrapper's CLI invocations to stderr.

## Build

```bash
./build_workflow.sh
```

Copies `list_reports.py`, `action.py`, `edisclosure_bin.py`,
`relative_time_ru.py`, `info.plist`, and `icon.png` into `dist/workflow/` and zips
them into `dist/alfred-e-disclosure.alfredworkflow`. No venv, no vendored `lib/`.

## Layout

- `list_reports.py` — Script Filter formatter: branches autocomplete vs listing,
  calls the CLI, renders Alfred JSON.
- `action.py` — action script: arm / force-refresh / download+open|save.
- `edisclosure_bin.py` — locates the CLI and owns the "not installed" guard.
- `relative_time_ru.py` — Russian relative-time formatter for the cache-age badge.
- `info.plist` — workflow definition (3 Script Filters + 1 action).

## Testing

There are no automated tests in the workflow — it is a trivial JSON→Alfred
reshape, and the heavy fetch/anti-bot/parser stack is tested in the CLI
repository against its injectable fake backend. Verification is done live:

- FAKE-smoke: drive the CLI's `EDISCLOSURE_FAKE_HTML_DIR` seed through the
  formatter across the four statuses and eyeball the Alfred JSON.
- Live run in Alfred: build, install, and exercise autocomplete → listing →
  open → save, plus one challenge → arm.
