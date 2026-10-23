# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Security is a high priority for this project. If you discover a vulnerability or security issue, please do not disclose it publicly in issues or pull requests.

Instead, please report it privately:
- Open a GitHub Private Security Advisory via **Security > Report a vulnerability**, or
- Contact the project maintainers directly via email.

Please provide:
1. Description of the vulnerability and its potential impact.
2. Step-by-step instructions or proof-of-concept to reproduce the behavior.
3. Suggested fix or mitigation, if available.

We will acknowledge receipt within 48 hours and work with you to release a fix promptly.

## Dependency auditing

CI runs `pip-audit` against `requirements.lock` on every push, on every pull request, and every Monday, because new advisories appear without any change here.

- **Scope: what ships.** Only `requirements.lock` is audited, which is everything the server runs. Dev tools (pytest, linters, pip-audit) and pip itself never run in production, so findings there would only be noise.
- **Threshold: any known advisory in that scope fails the job.** There is no severity cut-off, for two reasons. Many Python advisories carry no severity score, so a cut-off would quietly pass them. And severity does not say whether this app reaches the vulnerable code, which is the question that matters. The lock is about 25 packages, all loaded in the server, and a fix is usually a one-line version bump, so failing on everything stays manageable.
- **Fix by upgrading.** Bump the version in `requirements.lock`, run the tests, and commit. If an advisory looks unreachable but a fix exists, still upgrade rather than ignore it.
- **Ignore only when there is no usable fix.** Add `--ignore-vuln <ID>` to the audit step, with a comment saying why the code path is not reachable here and a date to check again. Remove it once a fix is released.
- The audit is a separate job, so a newly published advisory shows up as "Dependency audit" failing, not as failed tests.
