"""Store immutable display metadata on each uploaded guidance version."""

from alembic import op

revision = "006_source_version_metadata"
down_revision = "005_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source_versions ADD COLUMN title text")
    op.execute("ALTER TABLE source_versions ADD COLUMN category text")
    op.execute("""
        UPDATE source_versions v
        SET title = s.title, category = s.category
        FROM sources s
        WHERE s.org_id = v.org_id AND s.id = v.source_id
    """)
    op.execute("ALTER TABLE source_versions ALTER COLUMN title SET NOT NULL")
    op.execute("ALTER TABLE source_versions ALTER COLUMN category SET NOT NULL")
    op.execute("""
        ALTER TABLE source_versions ADD CONSTRAINT source_versions_category_valid
        CHECK (category IN ('guidelines','facts','terminology','messaging'))
    """)


def downgrade() -> None:
    raise RuntimeError("Source version metadata is immutable; use a forward repair migration")
