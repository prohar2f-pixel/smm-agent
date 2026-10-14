# Content Factory completion audit

Date: 2026-10-14
Specification: `smm-agent-content-factory-master-spec-v2.md`
Implementation plan: `docs/superpowers/plans/2026-10-13-content-factory-production.md`
Production commit at audit start: `983ab8410f98aca85636a72d8e91249a285c6642`

## Scope ruling

The delivered scope is the implementation plan's production objective: complete the
safe screen-demo execution vertical, add Text Factory and later media branches behind
disabled flags, deploy the integrated runtime, and make every external action fail
closed. Master-spec package 6 is a real business pilot and requires owner-selected
sources, thresholds and exact human publication approvals. It is not replaced with
synthetic evidence and no public send is performed by this audit.

## Package evidence

| Package | Authoritative implementation evidence | Verification | Result |
|---|---|---|---|
| 1. Production schema | Migration 007 and `domain/production_models.py` | Production schema and immutability tests | Complete |
| 2. Worker contracts | `workers/contracts.py`, `workers/report_service.py` | Fence, lease, hash, path, duplicate and conflict tests | Complete |
| 3. Screen-demo | `workers/screen_demo.py`, `domain/qa_service.py` | 30-45 second, multi-clip, approval and real FFmpeg smoke | Complete |
| 4. Approvals | `domain/approval_service.py`, `app/callback_service.py` | Role, expiry, replay, stale revision, exact hash and full QA tests | Complete |
| 5. Safe publishing | `domain/publish_service.py`, Telegram/VK adapters | Independent platforms, verified handles, manual evidence and unknown reconciliation tests | Complete |
| 6. Measurement | `domain/measurement_service.py`, `reporting/pilot_report.py` | Missing vs zero, mature windows, baseline, caps and independent-package tests | Complete |
| 7. Text Factory | Migrations 008, idea/campaign/text services | Provenance, scoring, 14-day capacity, immutable variants and budget tests | Complete |
| 8. Media contracts | Migration 009 and three media planners | Offline carousel, preview-before-master, animatic and aspect tests | Complete as disabled contracts, per plan |
| 9. Analytics | `domain/analytics_service.py` | Comparability, missing metrics, fact/hypothesis and continuation tests | Complete |
| 10. Runtime | `app/runtime.py`, `workers/runner.py`, factory handlers | Disabled defaults, migration startup, recovery, exhaustion and composition tests | Complete |
| 11. Deployment safety | `deployment/preflight.py`, backup module and runbook | Preflight, migration-on-copy and verified backup/restore drill | Complete |
| 12. Review and launch | PRs #4 and #5, release evidence and systemd release | Independent review fix pass, 183 tests and production checks | Complete |

## Invariant audit

1. Authorization, allowed transitions and stale-version rejection are enforced by
   owner middleware, application services, role checks and compare-and-swap tests.
2. Inbox events, callbacks, worker reports, commands, reservations and provider
   evidence are idempotent and covered by duplicate tests.
3. Source lineage, evidence and visible hypotheses are enforced before readiness and
   publication.
4. Briefs, revisions, approvals, assets and packages are immutable or replaced by a
   new version. Production schema v12 adds both brief immutability triggers.
5. Decisions bind the exact revision, subject hash, role, target and expiry.
6. Script, preview/final and publish approvals are separate stages.
7. Telegram and VK publish jobs and outcomes are independent.
8. Paid calls require all applicable limits, upper bounds and an atomic reservation.
9. A provider timeout becomes `unknown_outcome`; the consumer never blindly retries it.
10. Measured zero and missing data remain distinct in storage and reports.
11. Stop, failure, rollback and reconciliation preserve immutable history.
12. Workers and adapters receive bounded contracts and cannot derive permissions from
    source or model text.
13. No secret-pattern candidate was found in the tracked release tree. Runtime secrets
    remain in the protected environment file and are not copied into evidence.
14. Machine QA cannot approve meaning, final content or publication. Exact human
    decisions remain mandatory.

## Independent review closure

The independent whole-branch review found one Critical output-path issue and several
Important binding, retry, publishing, QA and pilot-verdict issues. The fix pass added
descriptor-relative output handling, canonical manifest binding, real multi-clip
assembly, supervised retries, concrete transports, operator reconciliation, persisted
QA and stricter independent pilot criteria. Every Critical and Important item received
a regression test and the whole suite passed afterward. One redundant read-only pilot
report query is deferred as a behavior-neutral minor.

## Fresh verification

- Current branch tree equals GitHub `main` tree.
- Production release file list equals GitHub `main` file list.
- Unit/integration suite: 183 tests passed.
- Compilation: passed.
- Diff whitespace check: passed.
- Real renderer smoke: 36 seconds, 1080x1920, two clips, audio and subtitles.
- Production SQLite: `integrity=ok`, schema v12.
- Service: active/running with zero automatic restarts.
- Telegram polling and all 11 factory commands: verified.
- Publish attempts: 0. External operations: 0. Queued publications: 0.
- Action flags: worker OFF, publishing OFF, paid generation OFF, legacy publishers OFF.

## Remaining non-code activation work

The implementation objective is complete. A real pilot is intentionally not claimed.
Before enabling production actions, the owner must select the pilot platform, target,
audience, offer, CTA, roles, thresholds and measured baseline; register three distinct
rights-cleared sources; then approve each exact script, preview, master and publish
package. Until those facts exist, preflight and disabled flags keep all sends closed.
