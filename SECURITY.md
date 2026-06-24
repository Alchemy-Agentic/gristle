# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Gristle, please report it privately so
it can be addressed before public disclosure.

- **Email:** paul@alchemyagentic.ai
- Alternatively, use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
  on this repository.

Please include steps to reproduce, the affected version or commit, and the potential
impact. We aim to acknowledge reports within a few business days.

Do **not** open a public issue for security problems.

## Supported versions

Gristle is pre-1.0 (`0.x`). Security fixes are applied to the latest release on the
`main` branch.

## Scope and deployment notes

- Gristle exposes its tools over MCP. When running the HTTP transport on a shared or
  public network, set `GRISTLE_API_KEY` to require bearer-token authentication.
- Gristle connects to a FalkorDB instance you provide; secure that instance
  (network isolation, authentication) according to your own deployment requirements.
- Never commit real secrets. Use `.env` (which is gitignored) for local configuration;
  `.env.example` documents the available settings with empty values.
