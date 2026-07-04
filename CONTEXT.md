# Context — Alfred e-Disclosure

The workflow is a thin wrapper around the globally installed `edisclosure` CLI
(`uv tool install edisclosure`). It fetches nothing itself: it shells out to the
CLI and reshapes stdout into Alfred Script Filter JSON. The whole anti-bot /
fetch / cache / download glossary now lives in the CLI repository
(`0xBADC0FFEE/edisclosure`).

## Keyword → CLI verb → Alfred rows

| Keyword  | Standard | Autocomplete call            | Listing call                                              |
| -------- | -------- | ---------------------------- | -------------------------------------------------------- |
| `msfo`   | `msfo`   | `edisclosure tickers`        | `edisclosure list <t> --standard msfo --async`           |
| `rsbu`   | `rsbu`   | `edisclosure tickers`        | `edisclosure list <t> --standard rsbu --async`           |
| `annual` | `annual` | `edisclosure tickers`        | `edisclosure list <t> --standard annual --async`         |

The formatter picks **autocomplete vs listing** before calling the CLI: a bare
ticker prefix (no trailing space, not an exact ticker) autocompletes; anything
else lists.

## Listing envelope status → Alfred rows

The `list --async` envelope is `{status, ticker, standard, fetched_at, items}`.

| Status      | Rows                                                                     | rerun |
| ----------- | ------------------------------------------------------------------------ | ----- |
| `ok`        | report rows; `⌘`=save, `⌥`=refresh (with cache age)                      | —     |
| `stale`     | report rows if any, else an "Обновляем…" placeholder                     | 0.5   |
| `challenge` | "пройти проверку" row (↵ → `arm`) above the saved rows                    | —     |
| `error`     | "сбросить и повторить" row (↵/⌥↵ → force refresh) above the saved rows    | —     |

## Actions

The selected row's payload drives `action.py`:

- `{arm}` → `edisclosure arm` (headed human solve).
- `{force_refresh}` → `edisclosure list --force-refresh --async` (fresh worker).
- `{ticker, standard, url}` → `edisclosure download --url` → `pdfs[0]` → `open`
  (or `cp ~/Downloads` when `⌘` set `save`).

## Field-name bridge

A listing item carries `file_url`; the action payload renames it to `url`. This
is the one silent break point if the CLI ever renames its fields — verified live,
not by an automated test.
