-- Hide a deleted pipeline without dropping its memos or documents; its name can be used again.
ALTER TABLE app.workspace_projects ADD COLUMN deleted_at timestamptz;
DROP INDEX app.workspace_project_name;
CREATE UNIQUE INDEX workspace_project_name ON app.workspace_projects (lower(name)) WHERE deleted_at IS NULL;
