"""Enable pgvector and UUID functions before creating typed tables."""

from alembic import op

revision = "001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    # Extensions can be shared by other schemas; never drop them here.
    pass
