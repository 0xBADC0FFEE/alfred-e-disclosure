# Context — Alfred e-Disclosure

Glossary for the e-disclosure.ru report-fetching workflow. Terms only — no
implementation. See `list_reports.py` for the code.

## Anti-bot terms

- **Challenge** — ServicePipe (Cybert) serves an anti-bot page instead of the
  real filings listing. Detected by absence of the `files-table` marker plus a
  known challenge marker. Two distinct flows exist (below).

- **Flow A / PoW arming** — the computational challenge: a spinner interstitial
  whose JavaScript proof-of-work a *real browser* clears on its own, no human.
  Bare `curl_cffi` (no JS engine) cannot. Arms the ServicePipe cookies.

- **Flow B / rotate-captcha** — the "Разверните картинку горизонтально" image
  challenge. Requires a **human** to solve (drag image upright). No working
  automated solver as of 2026-07-04 (RotNetR/servicepipe-solver Flow B broken
  upstream — see `.out-of-scope/servicepipe-solver.md`, issue #12). The browser
  fetch path hits Flow B, so a human is in the loop every time it appears.

- **ServicePipe cookies** — `spsc`, `spjs`, `spid` (also `spca`, `spcajs`,
  `rndcaptcha`). Set by challenge JavaScript, *not* `Set-Cookie`. Once obtained
  ("armed"), they authorise subsequent requests to the origin: one solve serves
  many fetches until they expire. Portable from a real browser to `curl_cffi`
  (this is what the manual `EDISCLOSURE_COOKIE` path already relies on).

- **Armed session** — a request context (browser or `curl_cffi`) carrying valid
  ServicePipe cookies, so the origin returns real content, not a challenge.

- **Harvest** — reading the ServicePipe cookies out of a browser context after a
  human clears Flow B, so they can be reused elsewhere.

- **Arming surface** — the browser (StealthyFetcher) that clears a challenge and
  acquires ServicePipe cookies. Runs **headless** in the background refresh chain
  (clears Flow A automatically; fails on Flow B) and **headed** for the human
  solve (Enter on the challenge row → human clears Flow B). Same machinery, only
  the `headless` flag differs.

- **Fetching surface** — `curl_cffi`, which does the actual (fast) filing
  fetches once armed. One arm serves both `type=4` and `type=3` and every
  subsequent refresh until the cookies die.

- **Handoff** — moving an armed session from the arming surface to the fetching
  surface by harvesting the cookies and injecting them into `curl_cffi`.
  Requires a matching Chrome TLS/HTTP2 fingerprint on the receiving side —
  ServicePipe blocks a plain-Python TLS handshake regardless of cookies.

- **Self-healing expiry** — armed cookies have no known TTL, so none is guessed.
  When they die, the next fetch is simply a fresh challenge (`Status.CHALLENGE`),
  which re-surfaces the solve row. A dead cookie produces the same state that
  started the loop.

## Fetch terms

- **МСФО / РСБУ** — IFRS / RAS filing types. МСФО is read from both `type=4`
  and `type=3` portal pages; РСБУ only from `type=3`.

- **Refresh worker** — background process (issues #10/#11) that repopulates the
  parsed-report cache. Must degrade gracefully (stale cache / terminal-error
  badge) rather than hang or spin forever when it cannot arm a session.
