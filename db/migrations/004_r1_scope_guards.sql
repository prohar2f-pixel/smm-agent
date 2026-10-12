ALTER TABLE reviews ADD COLUMN checklist_version TEXT;

CREATE TRIGGER guard_project_source_scope
BEFORE INSERT ON content_project_sources
BEGIN
    SELECT CASE WHEN
        (SELECT pilot_id FROM content_projects WHERE id = NEW.project_id)
        IS NOT
        (SELECT pilot_id FROM sources WHERE id = NEW.source_id)
    THEN RAISE(ABORT, 'project source pilot mismatch') END;
END;

CREATE TRIGGER guard_brief_source_membership
BEFORE INSERT ON content_brief_sources
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM content_briefs b
        JOIN content_project_sources ps ON ps.project_id = b.project_id
        WHERE b.id = NEW.brief_id AND ps.source_id = NEW.source_id
    ) THEN RAISE(ABORT, 'brief source is outside project') END;
END;

CREATE TRIGGER guard_revision_scope
BEFORE INSERT ON content_revisions
BEGIN
    SELECT CASE WHEN
        (SELECT project_id FROM content_briefs WHERE id = NEW.brief_id) != NEW.project_id
    THEN RAISE(ABORT, 'revision brief project mismatch') END;
    SELECT CASE WHEN NEW.parent_revision_id IS NOT NULL AND
        (SELECT project_id FROM content_revisions WHERE id = NEW.parent_revision_id) != NEW.project_id
    THEN RAISE(ABORT, 'revision parent project mismatch') END;
    SELECT CASE WHEN NEW.revision_no = 1 AND NEW.parent_revision_id IS NOT NULL
    THEN RAISE(ABORT, 'first revision cannot have parent') END;
    SELECT CASE WHEN NEW.revision_no > 1 AND (
        NEW.parent_revision_id IS NULL OR
        (SELECT revision_no FROM content_revisions WHERE id = NEW.parent_revision_id) != NEW.revision_no - 1
    ) THEN RAISE(ABORT, 'revision parent must be preceding revision') END;
    SELECT CASE WHEN NEW.revision_no !=
        COALESCE((SELECT MAX(revision_no) + 1 FROM content_revisions WHERE project_id = NEW.project_id), 1)
    THEN RAISE(ABORT, 'revision number must be next') END;
END;

CREATE TRIGGER guard_asset_scope
BEFORE INSERT ON assets
BEGIN
    SELECT CASE WHEN NEW.source_id IS NOT NULL AND
        (SELECT pilot_id FROM sources WHERE id = NEW.source_id) !=
        (SELECT pilot_id FROM content_projects WHERE id = NEW.project_id)
    THEN RAISE(ABORT, 'asset source pilot mismatch') END;
    SELECT CASE WHEN NEW.source_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM content_project_sources
        WHERE project_id = NEW.project_id AND source_id = NEW.source_id
    ) THEN RAISE(ABORT, 'asset source is outside project') END;
    SELECT CASE WHEN NEW.revision_id IS NOT NULL AND
        (SELECT project_id FROM content_revisions WHERE id = NEW.revision_id) != NEW.project_id
    THEN RAISE(ABORT, 'asset revision project mismatch') END;
END;

CREATE TRIGGER guard_evidence_scope
BEFORE INSERT ON claim_evidence
BEGIN
    SELECT CASE WHEN
        (SELECT s.pilot_id FROM sources s WHERE s.id = NEW.source_id) !=
        (SELECT p.pilot_id FROM claims c
         JOIN content_revisions r ON r.id = c.revision_id
         JOIN content_projects p ON p.id = r.project_id
         WHERE c.id = NEW.claim_id)
    THEN RAISE(ABORT, 'evidence source pilot mismatch') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM claims c
        JOIN content_revisions r ON r.id = c.revision_id
        JOIN content_project_sources ps
          ON ps.project_id = r.project_id AND ps.source_id = NEW.source_id
        WHERE c.id = NEW.claim_id
    ) THEN RAISE(ABORT, 'evidence source is outside project') END;
    SELECT CASE WHEN NEW.asset_id IS NOT NULL AND
        (SELECT a.project_id FROM assets a WHERE a.id = NEW.asset_id) !=
        (SELECT r.project_id FROM claims c
         JOIN content_revisions r ON r.id = c.revision_id
         WHERE c.id = NEW.claim_id)
    THEN RAISE(ABORT, 'evidence asset project mismatch') END;
END;
