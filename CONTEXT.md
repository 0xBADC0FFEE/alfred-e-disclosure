# Context — Alfred e-Disclosure

Glossary for the e-disclosure.ru report-fetching workflow. Terms only — no
implementation. See `list_reports.py` for the code.

## Anti-bot terms

- **Challenge** — ServicePipe (Cybert) serves an anti-bot page instead of the
  real filings listing. Detected by absence of the `files-table` marker plus a
  known challenge marker. Two distinct flows exist (below).

- **Flow A / PoW arming** — the computational challenge: a spinner interstitial
  whose JavaScript proof-of-work a *real browser* clears on its own, no human.
  Bare `curl_cffi` (no JS engine) cannot. Arms the persistent profile.

- **Flow B / rotate-captcha** — the "Разверните картинку горизонтально" image
  challenge. Requires a **human** to solve (drag image upright). No working
  automated solver as of 2026-07-04 (RotNetR/servicepipe-solver Flow B broken
  upstream — see `.out-of-scope/servicepipe-solver.md`, issue #12). The browser
  fetch path hits Flow B, so a human is in the loop every time it appears.

- **ServicePipe cookies** — `spsc`, `spjs`, `spid` (also `spca`, `spcajs`,
  `rndcaptcha`). Set by challenge JavaScript, *not* `Set-Cookie`, into the
  browser context. Once cleared ("armed"), they authorise subsequent requests to
  the origin until they expire. They stay bound to the browser's TLS/JS context,
  so — as the failed cookie-handoff proved — they do **not** transplant into
  `curl_cffi`; they live in the persistent profile instead.

- **Persistent profile** — a camoufox `user_data_dir` on disk
  (`cache_dir.root()/camoufox-profile`) that every browser launch reuses. It is
  the bridge across process boundaries: the headed human solve writes the armed
  ServicePipe session into it, and a later headless refresh (a *different*
  process) reopens the same profile already armed. One solve serves both
  `type=4` and `type=3` and every refresh until the session dies. No cookie
  harvest, no `curl_cffi` handoff.

- **Armed session** — the profile (or a browser open on it) carrying valid
  ServicePipe cookies, so the origin returns real content, not a challenge.

- **Arming surface** — the browser (StealthyFetcher) bound to the persistent
  profile that clears a challenge and does the real fetch. Runs **headless** in
  the background refresh chain (clears Flow A automatically; fails on Flow B) and
  **headed** for the human solve (Enter on the challenge row → human clears
  Flow B). Same machinery, only the `headless` flag differs. Both share the one
  profile, so access is serialised by an origin-wide lock (below).

- **Profile lock** — Playwright forbids two instances on one `user_data_dir`, so
  a single origin-wide mutex (`refresh_lock` key `browser-profile`) guards every
  browser open, re-entrant within the owning PID. A background worker that finds
  the profile held by another process skips the browser and serves stale rather
  than crashing.

- **Self-healing expiry** — the armed session has no known TTL, so none is
  guessed. When it dies, the next fetch is simply a fresh challenge
  (`Status.CHALLENGE`), which re-surfaces the solve row. A dead session produces
  the same state that started the loop.

## Fetch terms

- **МСФО / РСБУ** — IFRS / RAS filing types. МСФО is read from both `type=4`
  and `type=3` portal pages; РСБУ only from `type=3`.

- **Refresh worker** — background process (issues #10/#11) that repopulates the
  parsed-report cache. Must degrade gracefully (stale cache / terminal-error
  badge) rather than hang or spin forever when it cannot arm a session.
