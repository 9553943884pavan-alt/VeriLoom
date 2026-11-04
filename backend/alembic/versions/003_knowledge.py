"""Versioned approved guidance and 384-dimensional E5 passages."""

from alembic import op

revision = "003_knowledge"
down_revision = "002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        CREATE TABLE sources (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            title text NOT NULL,
            category text NOT NULL,
            created_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            archived_at timestamptz,
            UNIQUE (org_id, id),
            CONSTRAINT sources_category_valid CHECK (category IN ('guidelines','facts','terminology','messaging'))
        );
        CREATE TABLE source_versions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            source_id uuid NOT NULL,
            version_label text NOT NULL,
            r2_object_key text UNIQUE,
            origin_path text,
            sha256 char(64) NOT NULL,
            mime_type text,
            byte_size bigint,
            effective_from date NOT NULL,
            effective_to date,
            ingestion_status text NOT NULL DEFAULT 'queued',
            approval_status text NOT NULL DEFAULT 'pending',
            ingestion_error_code text,
            uploaded_by_user_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            ready_at timestamptz,
            approved_at timestamptz,
            archived_at timestamptz,
            UNIQUE (org_id, id),
            UNIQUE (org_id, source_id, id),
            UNIQUE (source_id, version_label),
            FOREIGN KEY (org_id, source_id) REFERENCES sources(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT source_versions_content_source CHECK (
                (origin_path IS NOT NULL AND r2_object_key IS NULL)
                OR (origin_path IS NULL AND r2_object_key IS NOT NULL
                    AND uploaded_by_user_id IS NOT NULL AND mime_type IS NOT NULL
                    AND byte_size IS NOT NULL)
            ),
            CONSTRAINT source_versions_byte_size_valid CHECK (byte_size IS NULL OR byte_size > 0),
            CONSTRAINT source_versions_effective_dates_valid CHECK (effective_to IS NULL OR effective_to > effective_from),
            CONSTRAINT source_versions_ingestion_status_valid CHECK (ingestion_status IN ('queued','processing','ready','failed')),
            CONSTRAINT source_versions_approval_status_valid CHECK (approval_status IN ('pending','approved','archived')),
            CONSTRAINT source_versions_approved_ready CHECK (approval_status <> 'approved' OR ingestion_status = 'ready')
        );
        CREATE UNIQUE INDEX source_versions_one_approved_idx ON source_versions(source_id)
            WHERE approval_status = 'approved';
        CREATE INDEX source_versions_eligible_idx
            ON source_versions(org_id, approval_status, effective_from, effective_to);
        CREATE TABLE source_passages (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            source_id uuid NOT NULL,
            version_id uuid NOT NULL,
            source_ref text NOT NULL,
            chunk_index integer NOT NULL,
            title text NOT NULL,
            text_content text NOT NULL,
            start_offset integer NOT NULL,
            end_offset integer NOT NULL,
            category text NOT NULL,
            embedding vector(384) NOT NULL,
            embedding_model text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            UNIQUE (version_id, source_ref, chunk_index),
            FOREIGN KEY (org_id, source_id) REFERENCES sources(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, source_id, version_id)
                REFERENCES source_versions(org_id, source_id, id) ON DELETE RESTRICT,
            CONSTRAINT source_passages_chunk_index_valid CHECK (chunk_index >= 0),
            CONSTRAINT source_passages_offsets_valid CHECK (start_offset >= 0 AND end_offset > start_offset),
            CONSTRAINT source_passages_text_nonempty CHECK (btrim(text_content) <> '')
        );
        CREATE INDEX source_passages_org_version_idx ON source_passages(org_id, version_id);
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Knowledge migration is append-only; preserve cited passages")
