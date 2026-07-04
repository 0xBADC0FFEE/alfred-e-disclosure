# Persistent browser profile for session reuse — best practices (research 2026-07-04)

Study behind the pivot from cookie-harvest→curl_cffi to a persistent camoufox
profile. Sources: Exa web search over camoufox/Playwright issues, scrapling docs,
Playwright docs, anti-bot session-persistence writeups.

## Verdict
Reuse the solved ServicePipe session via a **persistent `user_data_dir` profile**
that both the headed solve and the headless refresh open — no cookie handoff.

## Q1 — concurrency / profile locking (DECIDED: origin-wide mutex)
- Playwright docs (Chromium & Firefox): *"browsers do not allow launching
  multiple instances with the same User Data Directory."* Firefox → "Firefox is
  already running" + `TargetClosedException`; Chromium → silent singleton-lock.
  Refs: playwright#35216, #2828, #36007, SO 77967053.
- Multi-agent frameworks (openbrowser, FlareSolverr, browserless) converge:
  serialize access to one profile; parallel needs *replicas* (copies), not shared
  dirs — "directly sharing the same profile_dir across processes risks
  singleton-lock failures and profile database corruption."
- **Decision**: one process may touch the profile at a time. A single origin-wide
  lock (`refresh_lock` key `browser-profile`), re-entrant within the owning PID,
  guards every `StealthySession` open. A background worker that finds the profile
  owned by another process skips the browser (serves stale) instead of crashing.

## Q3 — fingerprint stability across restarts (DECIDED: rely on cookies now, pin later)
- Known camoufox bug (#71): `persistent_context=True` alone does NOT keep the
  fingerprint stable across restarts — window size + fingerprint reroll each launch.
- Proven fix (#38, #328, apify/camoufox-js#55): serialize `launch_options()` to a
  `fingerprint.json` once, reload via `from_options=` on later launches. Verified
  with `camoufox[geoip]>=0.4.11`. `fonts:spacing_seed` also affects the id.
- BUT scrapling's `StealthyFetcher.fetch(user_data_dir=...)` generates a fresh
  fingerprint per call; it does not expose `from_options`.
- What actually carries trust is the **profile's cookies / localStorage /
  IndexedDB**, not the fingerprint (CaptchaAI: "persistent profile (days old) →
  very low CAPTCHA frequency"). The solved ServicePipe cookies live in the profile
  regardless of fingerprint reroll.
- **Decision**: ship relying on profile cookies. If the live test shows the reroll
  gets re-challenged, escalate to pinned fingerprint via
  `launch_options()`→`fingerprint.json`→`from_options` (documented, ~10 lines).

## scrapling specifics
- `user_data_dir` "**Only Works with sessions**" — `StealthyFetcher.fetch` counts
  (it wraps `with StealthySession(**kwargs)`), so passing `user_data_dir=` to
  `.fetch` is valid.
- Session benefits list "consistent fingerprint" — but only *within* one session
  (one process), which is the #71 cross-process gap.
- `cookies=` param exists on the session if a boost is ever needed (not used now).

## The one thing only a live human solve can confirm
Second, headless launch against the same profile reaches `files-table` without a
fresh challenge — i.e. ServicePipe's session survives the process boundary through
the profile. That is the whole bet; only a real solve + refresh proves it.
