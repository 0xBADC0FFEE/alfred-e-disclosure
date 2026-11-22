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

When a status yields no rows of its own, its placeholder stays actionable: ↵ and
⌥↵ both send `{force_refresh}`.

## Actions

The selected row's payload drives `action.py`:

- `{arm}` → `edisclosure arm` (headed human solve).
- `{force_refresh}` → `edisclosure list --force-refresh --async` (fresh worker).
- `{ticker, standard, url}` → `edisclosure download --url` → every path in `pdfs`
  → `open` (or `cp ~/Downloads` when `⌘` set `save`).

The `download` envelope is `{status, pdfs}`; `pdfs` is always a list, because one
listing row is one archive URL and an archive may hold several independent
documents. `status != "ok"` or an empty `pdfs` is an error. Opening walks the list
backwards so the first document — the emitter's own ordering, our only priority
signal — ends up frontmost. Saving aborts on the first refusal, naming the file;
what was already copied stays in `~/Downloads`.

## Field-name bridge

A listing item carries `file_url`; the action payload renames it to `url`. This
is the one silent break point if the CLI ever renames its fields — verified live,
not by an automated test.
