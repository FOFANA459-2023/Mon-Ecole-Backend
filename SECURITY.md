# Security policy

Mon École holds personal data about children, families and staff. Please report security problems privately.

## Reporting a vulnerability

- Use **GitHub → Security → Report a vulnerability** on this repository (private advisory), or email the maintainer.
- Include what you found, how to reproduce it and the impact you expect. Do not access real school data or
  degrade the service while testing; use a local copy (`docker compose up` + `seed_demo`).
- You will get an answer within 3 working days. Please give us reasonable time to fix before disclosure.

## What is checked automatically

Every pull request runs unit, integration and security tests, static analysis (semgrep), dependency audits,
a secret scan of the whole history (gitleaks), Docker image scans (trivy, hadolint) and, for the API, an
authenticated OWASP ZAP scan. Dependabot proposes dependency updates weekly.
