# Security

## How the tool protects itself

- Everything runs locally. The server only listens on `127.0.0.1`, validates `Host` and `Origin`, requires
  a per-run session token on `/api/` routes and makes no network calls at run time (no telemetry, no CDN).
- Decompiled code, the AndroidManifest and APK strings are treated as untrusted input: XML DTDs and
  entities are refused, symbolic links are ignored, file sizes and XML depth are capped, and text reaches
  the browser only through `textContent` under a strict Content Security Policy.
- Analyst notes and the index cache are stored in the user data and cache directories, never inside the
  analysed folder.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting ("Report a vulnerability" in the repository's *Security* tab)
instead of a public issue. Include the version (`jadx-atlas --version`), the steps to reproduce and, if
possible, a minimal input that does not contain third-party code.

I try to reply within 14 days. Fixes are released before the details are made public, and reporters are
credited if they want to be.
