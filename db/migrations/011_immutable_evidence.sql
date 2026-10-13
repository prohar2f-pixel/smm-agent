CREATE TRIGGER immutable_approval_decisions_update
BEFORE UPDATE ON approval_decisions
BEGIN SELECT RAISE(ABORT, 'approval decisions are immutable'); END;

CREATE TRIGGER immutable_approval_decisions_delete
BEFORE DELETE ON approval_decisions
BEGIN SELECT RAISE(ABORT, 'approval decisions are immutable'); END;

CREATE TRIGGER immutable_content_revisions_update
BEFORE UPDATE ON content_revisions
BEGIN SELECT RAISE(ABORT, 'content revisions are immutable'); END;

CREATE TRIGGER immutable_content_revisions_delete
BEFORE DELETE ON content_revisions
BEGIN SELECT RAISE(ABORT, 'content revisions are immutable'); END;

CREATE TRIGGER immutable_asset_evidence_update
BEFORE UPDATE ON assets
WHEN NEW.id != OLD.id OR NEW.project_id != OLD.project_id
 OR COALESCE(NEW.source_id, '') != COALESCE(OLD.source_id, '')
 OR COALESCE(NEW.revision_id, '') != COALESCE(OLD.revision_id, '')
 OR NEW.role != OLD.role OR NEW.storage_key != OLD.storage_key
 OR NEW.sha256 != OLD.sha256 OR NEW.mime_type != OLD.mime_type
 OR NEW.size_bytes != OLD.size_bytes
 OR COALESCE(NEW.duration_ms, -1) != COALESCE(OLD.duration_ms, -1)
 OR COALESCE(NEW.width, -1) != COALESCE(OLD.width, -1)
 OR COALESCE(NEW.height, -1) != COALESCE(OLD.height, -1)
 OR COALESCE(NEW.created_by_job_id, '') != COALESCE(OLD.created_by_job_id, '')
 OR NEW.created_at != OLD.created_at
BEGIN SELECT RAISE(ABORT, 'asset evidence is immutable'); END;

CREATE TRIGGER immutable_assets_delete
BEFORE DELETE ON assets
BEGIN SELECT RAISE(ABORT, 'assets are immutable'); END;
