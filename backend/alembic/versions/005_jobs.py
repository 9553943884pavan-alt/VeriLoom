"""Durable worker queue and idempotent mutation replay records."""

from alembic import op

revision = "005_jobs"
down_revision = "004_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        CREATE TABLE jobs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            kind text NOT NULL,
            resource_id uuid NOT NULL,
            state text NOT NULL DEFAULT 'queued',
            attempts integer NOT NULL DEFAULT 0,
            run_after timestamptz NOT NULL DEFAULT now(),
            lease_until timestamptz,
            worker_id text,
            last_error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            CONSTRAINT jobs_kind_valid CHECK (kind IN ('ingest_source','review_revision','answer_question')),
            CONSTRAINT jobs_state_valid CHECK (state IN ('queued','running','succeeded','failed')),
            CONSTRAINT jobs_attempts_valid CHECK (attempts >= 0)
        );
        CREATE UNIQUE INDEX jobs_one_active_idx ON jobs(kind, resource_id)
            WHERE state IN ('queued','running');
        CREATE INDEX jobs_due_idx ON jobs(run_after, id) WHERE state = 'queued';
        CREATE INDEX jobs_expired_lease_idx ON jobs(lease_until, id) WHERE state = 'running';
        CREATE TABLE idempotency_records (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            endpoint text NOT NULL,
            key uuid NOT NULL,
            request_sha256 char(64) NOT NULL,
            response_status integer,
            response_json jsonb,
            resource_id uuid,
            state text NOT NULL DEFAULT 'in_progress',
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            UNIQUE (org_id, id),
            UNIQUE (user_id, endpoint, key),
            CONSTRAINT idempotency_records_state_valid CHECK (state IN ('in_progress','complete')),
            CONSTRAINT idempotency_records_expiry_valid CHECK (expires_at > created_at)
        );
        CREATE INDEX idempotency_records_expiry_idx ON idempotency_records(expires_at);
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Drain workers and use a forward repair migration")
