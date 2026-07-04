# servicepipe-solver as the ServicePipe anti-bot strategy

This project does not bundle or depend on `servicepipe-solver` to pass the
`e-disclosure.ru` ServicePipe challenge. Report loading falls back to
`EDISCLOSURE_COOKIE` (manual browser cookies) → `StealthyFetcher`.

## Why this is out of scope

`servicepipe-solver` (v0.1.0) solves the challenge in two flows:

- **Flow A** — arm the PoW cookies (`spsc` / `spid` / signed `spjs`).
- **Flow B** — the rotate-CAPTCHA ("Разверните картинку горизонтально"),
  solved locally by the RotNetR model.

Verified live against `SBER` (`id=3043`) on 2026-07-04, driving the solver over
our real `curl_cffi` Chrome TLS/HTTP2 transport:

- **Flow A works** — produces genuine `spsc/spjs/spid/spsn/oirutpspid` cookies.
- **Flow B is broken upstream.** With the correct RotNetR weights loaded the
  model predicts sane angles, but the bless step never succeeds — the live
  deployment returns a challenge/404 regardless of the submitted angle. The
  bless URL/protocol baked into v0.1.0 no longer matches the deployed
  ServicePipe endpoint. The library's own `captcha.py` flags Flow B as
  EXPERIMENTAL.

Because Flow B never blesses, the challenge is never cleared, so the first and
central acceptance criterion of the request ("SBER passes the challenge and
fills the cache with real reports") cannot be met by this library. Making it
work means reverse-engineering the current bless endpoint — an upstream job, not
something the workflow can carry.

The cost of adopting it is also high and permanent: the `[captcha]` extra pulls
`torch` (~900 MB venv), plus placeholder model weights the wheel can't even
download (it ships a 404 URL; the real file lives at `lumina37/rotate-captcha-crack`
v0.5.1). That weight is a lot of moving parts to keep a broken path alive.

Cross-checked with a real, non-automated browser: the challenge currently
escalates straight to the rotate-CAPTCHA and requires a human solve. The
practical, working path is therefore a browser session cookie via
`EDISCLOSURE_COOKIE`, which the fallback chain already supports.

## If this is reconsidered

Reopen only if upstream fixes Flow B against the live e-disclosure deployment
(a working bless round-trip), or a different solver clears the rotate-CAPTCHA
headlessly. At that point the fetcher seam makes re-slotting a solver strategy
straightforward — the seam and fallback ordering from #11 stay in place.

## Prior requests

- #12 — "Отчёты снова грузятся сквозь ServicePipe (servicepipe-solver за флагом + цепочка фолбэков)" (parent #10)
