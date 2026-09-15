# AI NEWSROOM development handoff

## Preserved baseline

- Repository: `ceo538/hi9843`
- Baseline commit: `6dc5d002f1a91a5d6ae1095b4ce91372b96e1d45`
- Preservation branch: `v1-baseline`
- Development branch: `codex/newsroom-development`
- Application directory: `AI_NEWSROOM_GITHUB_RC`

The preservation branch was created and read back from GitHub during connection verification. Do not reset it or force-push over existing work. The baseline branch is a reference, not a claim of branch protection or immutable backup.

## Connection verification scope

This documentation commit verifies remote file creation without modifying the application or `main`. A draft pull request will be used to verify the review workflow. Actions dispatch, workflow-file modification, and repository administration permissions must be checked separately; contents write access does not certify those permissions.

No secret values belong in this document, commits, logs, or public artifacts. Do not change billing, automatic-review settings, or credit-spending settings as part of this check.

## Actual baseline scope

At the baseline commit, `app/main.py` serves health, a static module-name list, a dashboard, and a root response. The list returned by `/api/system` is not evidence that the listed operational modules are implemented.

The existing release workflow runs unit tests, a Windows browser check, model-evaluation and live-data scripts, and packages their results. Passing those checks establishes only the behavior those scripts actually test. It does not establish production readiness, news-to-article end-to-end correctness, comprehensive model quality, or legal clearance for commercial data redistribution.

## Development order

1. Inventory implemented code and test coverage before making completion claims.
2. Separate inexpensive local/CI checks from explicitly invoked live, billable API checks. Avoid duplicate live runs and redact secret-bearing errors before saving reports.
3. Implement persistent news/disclosure ingestion and deduplication, with source provenance and applicable usage restrictions.
4. Add market-reaction detection, company discovery and evidence-backed supply-chain paths. Preserve actual edge direction and distinguish a confirmed edge from an inferred end-to-end relationship.
5. Connect fact checking, counterargument review, interview questions, and article drafting; retain human editorial approval before publication.
6. Add dashboard and notification delivery, followed by integration and Windows deployment validation against the actual operational application.

## Reporting

Report code changes, test commands, environment, scope and results separately. Do not equate mock tests with live model evaluation. Raise owner-only blockers immediately: authentication/authorization, API issuance, billing/quotas, data-use agreements or account approvals. Never claim a background task is running without an actual running task or automation.
