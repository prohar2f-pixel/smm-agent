CREATE TRIGGER immutable_content_briefs_update
BEFORE UPDATE ON content_briefs
BEGIN SELECT RAISE(ABORT, 'content briefs are immutable'); END;

CREATE TRIGGER immutable_content_briefs_delete
BEFORE DELETE ON content_briefs
BEGIN SELECT RAISE(ABORT, 'content briefs are immutable'); END;
