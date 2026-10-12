# R1 Source, Brief and Evidence Core

## Outcome

R1 provides the first safe domain slice for a content factory. It can capture and verify source provenance, create versioned content inputs, attach evidence to factual claims and decide whether one exact revision is ready to enter production.

## Boundaries

The implemented path is:

1. `SourceService.capture_source` stores a pending source.
2. `SourceService.verify_source` records rights and privacy decisions using compare-and-swap on `row_version`.
3. `ContentService.create_project` accepts only allowed, privacy-clear sources from the same pilot.
4. `ContentService.create_brief` freezes a numbered brief and its proof-source links.
5. `ContentService.create_revision` freezes script, caption, timeline, prompt versions and canonical content hash.
6. `ReadinessService` appends claims, evidence and human reviews.
7. `check_production_ready` evaluates the requested revision, not its parent or the project's latest revision implicitly.

Briefs, revisions, claims, evidence and reviews are append-only through public APIs. Corrected content is represented by a new revision.

## Production blockers

The readiness gate returns sorted unique codes:

- `SOURCE_NOT_ALLOWED`
- `SOURCE_PRIVACY_BLOCKED`
- `CLAIM_EVIDENCE_MISSING`
- `HYPOTHESIS_LABEL_MISSING`
- `CLAIM_BLOCKED`
- `SCRIPT_APPROVAL_MISSING`
- `SUBJECT_HASH_MISMATCH`
- `CROSS_PILOT_EVIDENCE`

An approved review is valid only when its `subject_sha256` equals the immutable hash of the requested revision. Parent approvals never transfer to child revisions.

## Safety properties covered by tests

- Foreign keys and restrictive deletes preserve provenance.
- Invalid enum values and row versions are rejected by SQLite.
- Allowed rights require a non-empty evidence reference.
- Stale source verification cannot overwrite a newer decision.
- Cross-pilot sources and evidence are rejected.
- Project, brief and revision numbering conflicts fail closed.
- Supported claims need evidence.
- Hypotheses need a visible label location.
- Disputed, outdated and unverified claims block production.
- Revoked project sources are detected again at readiness time.
- Script approval is bound to the exact revision hash.

## Disabled posture

R1 is code and schema only. It is not connected to bot commands, callbacks, scheduler jobs, render workers, publication adapters or the production database. Existing posting behavior is unchanged. Activation requires a later integration package with feature flags, operator UX and deployment approval.

## Verification

Run from the repository root:

```bash
python3 -m py_compile db/content_repository.py domain/source_service.py domain/content_service.py domain/readiness_service.py
python3 -m unittest discover -v
git diff --check
```

The implementation was developed on `feat/content-factory-r0` starting after commit `56352bfe386687d1d7a18ab9f38140077b995572`. It was not merged, pushed or deployed as part of R1 development.
