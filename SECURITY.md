# Security Policy

## Supported versions

TesterKit is pre-1.0. Only the latest released `0.x` version receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for vulnerabilities. Use one of these private channels:

1. **Preferred:** GitHub [private vulnerability reporting](https://github.com/pragmatest-dev/testerkit/security/advisories/new).
2. Email `security@pragmatest.io`.

Include:

- A description of the issue and the impact you observed
- Steps to reproduce (minimal example if possible)
- Affected version(s)
- Any suggested mitigation

We will acknowledge receipt within 3 business days and aim to provide a status update within 10 business days. Once a fix is available, we will coordinate disclosure timing with the reporter.

## Scope

In scope: code in this repository (the `testerkit` package, its CLI, and its MCP/HTTP servers).

Out of scope: issues in third-party dependencies (please report upstream), misconfiguration of user-provided instrument drivers, and social-engineering attacks against maintainers.
