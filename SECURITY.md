# Security policy

## Supported versions

Security fixes are applied to the latest release on `main`.

## Reporting a vulnerability

**Please don't report security issues in public GitHub issues.**

Report them privately through GitHub's
[private vulnerability reporting](https://github.com/kalilfagundes/meta-ads-competitor-tracker/security/advisories/new)
(**Security → Report a vulnerability**). Please include:

- what the issue is and what an attacker could do with it,
- steps to reproduce, or a proof of concept,
- the affected version or commit.

You should get an acknowledgement within a few days. Once a fix is ready, we'll coordinate
disclosure with you and credit you if you'd like.

## Hardening your deployment

- Keep `.env` private (the setup script makes it readable only by you): it holds the database,
  storage and session secrets. Each container receives only the ones it needs.
  If you write `.env` by hand, use long random values.
- Serve the app over **HTTPS** behind a reverse proxy before exposing it to the internet
  (see [docs/configuration.md](docs/configuration.md#exposing-it-on-the-internet-https)).
- Don't set `FORWARDED_ALLOW_IPS=*`: it lets anyone fake their IP past the login limit
  (see [Forwarding headers](docs/configuration.md#exposing-it-on-the-internet-https)).
- Don't expose Postgres, or Garage's S3 and admin APIs, publicly.
- Proxy credentials entered in Settings are stored in the database, so protect your database
  backups accordingly.
