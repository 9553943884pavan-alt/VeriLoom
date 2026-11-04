"""Identity, organisations, membership, invitation, and opaque sessions."""

from alembic import op

revision = "002_identity"
down_revision = "001_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL UNIQUE,
            display_name text NOT NULL,
            password_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            disabled_at timestamptz,
            CONSTRAINT users_email_normalized CHECK (email = lower(btrim(email)) AND email <> '')
        );
        CREATE TABLE organisations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug text NOT NULL UNIQUE,
            name text NOT NULL,
            time_zone text NOT NULL,
            corpus_version bigint NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT organisations_corpus_version_nonnegative CHECK (corpus_version >= 0)
        );
        CREATE TABLE memberships (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            roles text[] NOT NULL,
            status text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            UNIQUE (org_id, user_id),
            CONSTRAINT memberships_roles_valid CHECK (
                cardinality(roles) > 0 AND roles <@ ARRAY['admin','creator','reviewer']
            ),
            CONSTRAINT memberships_status_valid CHECK (status IN ('active','disabled'))
        );
        CREATE INDEX memberships_user_org_idx ON memberships(user_id, org_id);
        CREATE INDEX memberships_org_status_idx ON memberships(org_id, status);
        CREATE TABLE invitations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            email text NOT NULL,
            roles text[] NOT NULL,
            token_hash bytea NOT NULL UNIQUE,
            invited_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            CONSTRAINT invitations_email_normalized CHECK (email = lower(btrim(email)) AND email <> ''),
            CONSTRAINT invitations_roles_valid CHECK (
                cardinality(roles) > 0 AND roles <@ ARRAY['admin','creator','reviewer']
            ),
            CONSTRAINT invitations_expiry_valid CHECK (expires_at > created_at)
        );
        CREATE INDEX invitations_org_email_idx ON invitations(org_id, email);
        CREATE TABLE sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            token_hash bytea NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz,
            CONSTRAINT sessions_expiry_valid CHECK (expires_at > created_at)
        );
        CREATE INDEX sessions_expires_at_idx ON sessions(expires_at);
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Identity migration is append-only; use a forward repair migration")
