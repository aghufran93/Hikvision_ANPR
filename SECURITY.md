# Security Policy

## Reporting a Vulnerability

This is a small, actively-used project monitoring live traffic cameras. If
you find a security issue:

1. **Do not open a public GitHub issue for it.**
2. Contact the maintainer directly (see the repository owner's GitHub
   profile for contact details) with a description of the issue and
   steps to reproduce.
3. Allow reasonable time for a fix before any public disclosure, given
   this runs against live production camera credentials and captures
   real vehicle/plate data.

## Known Risks (current, as of this document)

This section documents actual, verified characteristics of the code as it
exists today — not hypothetical concerns.

- **The dashboard has no authentication.** `dashboard.py` exposes
  `/api/events`, `/api/events/stream`, and `/images/<direction>/<file>`
  with no login, API key, or IP restriction of any kind. Anyone who can
  reach the configured port can view every captured plate, vehicle image,
  and `LPR_NOT_READ` event. If this is exposed beyond a trusted internal
  network, put it behind a reverse proxy that adds authentication.
- **TLS certificate verification is disabled for camera connections.**
  `anpr_common.py` connects to each camera with `requests.get(...,
  verify=False)`, appropriate for a camera's typical self-signed
  certificate, but this means a network-position attacker between the
  server and the camera would not be detected by certificate validation.
  This is currently an accepted risk, not a bug — documented here so it's
  an explicit decision rather than an overlooked one.
- **Camera credentials use HTTP Digest Auth**, not the strongest scheme
  available, but this is what the camera firmware supports over its
  ISAPI event stream endpoint.
- **`.env` (real credentials) is correctly gitignored** and has never
  been committed to this repository (verified against full git history)
  — no action needed here, noted for completeness.
- **No rate limiting or request size limits** are configured on the Flask
  dashboard beyond Flask's own defaults.
- **`/images/<direction>/<filename>` uses `flask.send_from_directory`**,
  which safely rejects path-traversal attempts (`../`) — this route is
  not currently believed to be vulnerable to directory traversal.

## Supported Versions

Only the current `main` branch is supported. There is no formal release/
version-tag process yet (see `CHANGELOG.md` for version history embedded
in code comments and commit history instead).
