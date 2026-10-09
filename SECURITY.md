# Security

## Responsible use

JADX Atlas is a static analysis tool for apps you are **authorised to analyse**: your own apps, open-source
apps, deliberately vulnerable apps (CTF, training) or bug bounty targets **within each programme's scope
and rules**. Decompiling or testing third-party apps without authorisation may break the law, terms of
service or copyright. Do not publish decompiled third-party code.

Atlas results are **candidates** for manual review, not verdicts. An inferred relation, role or finding can
be wrong; each one carries its confidence and evidence so you can check it.

## Threat model of the tool itself

- Everything runs locally. The server only listens on `127.0.0.1`, validates `Host` and `Origin`, requires
  a per-run session token on `/api/` routes, and makes no network calls at run time (no telemetry, no CDN).
- Decompiled code, the AndroidManifest and APK strings are treated as **hostile input**: XML DTDs and
  entities are refused, symbolic links are ignored, file sizes and XML depth are capped, and text reaches
  the browser only through `textContent` under a strict Content Security Policy.
- Analyst notes and the index cache are stored in your user data and cache directories, never inside the
  analysed folder.

## Reporting a vulnerability in JADX Atlas

Please use GitHub's **"Report a vulnerability"** (private vulnerability reporting) in the repository's
*Security* tab. Do not open a public issue for security problems. Include the version
(`jadx-atlas --version`), steps to reproduce and, if possible, a minimal input that does **not** contain
third-party code.

The maintainer aims to reply within 14 days. Fixes are released before public disclosure, and reporters
are credited if they wish.
