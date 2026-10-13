ALTER TABLE approval_decisions ADD COLUMN platform TEXT
    CHECK(platform IS NULL OR platform IN ('telegram','vk','manual'));

CREATE TRIGGER immutable_publish_packages_update
BEFORE UPDATE ON publish_packages
BEGIN SELECT RAISE(ABORT, 'publish packages are immutable'); END;

CREATE TRIGGER immutable_publish_packages_delete
BEFORE DELETE ON publish_packages
BEGIN SELECT RAISE(ABORT, 'publish packages are immutable'); END;

CREATE TRIGGER metric_measured_value_insert
BEFORE INSERT ON metric_snapshots
WHEN NEW.is_measured=1 AND NEW.value_numeric IS NULL
BEGIN SELECT RAISE(ABORT, 'measured metric requires a value'); END;

CREATE TRIGGER metric_measured_value_update
BEFORE UPDATE OF is_measured,value_numeric ON metric_snapshots
WHEN NEW.is_measured=1 AND NEW.value_numeric IS NULL
BEGIN SELECT RAISE(ABORT, 'measured metric requires a value'); END;
