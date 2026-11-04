"""Record the provider that actually generated each review or follow-up."""

from alembic import op

revision = "007_provider_attribution"
down_revision = "006_source_version_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reviews ADD COLUMN provider_id text")
    op.execute("ALTER TABLE chat_turns ADD COLUMN provider_id text")
    op.execute("ALTER TABLE chat_turns ADD COLUMN rewrite_provider_id text")
    op.execute("ALTER TABLE chat_turns ADD COLUMN rewrite_model_id text")
    # Historical calls ran only on Sarvam. Older handlers wrote model_id before
    # the call, including paths without a call, so backfill only completed work
    # with evidence and a generation route. Failed/ambiguous rows remain null.
    op.execute("""
        UPDATE reviews AS r SET provider_id='sarvam'
        WHERE r.model_id IS NOT NULL AND r.state IN ('ready_for_review','changes_requested','approved')
          AND EXISTS (SELECT 1 FROM review_evidence AS e WHERE e.org_id=r.org_id AND e.review_id=r.id)
    """)
    op.execute("""
        UPDATE chat_turns SET provider_id='sarvam'
        WHERE model_id IS NOT NULL AND state='completed'
          AND route IN ('reuse_evidence','rewrite_retrieve','fresh_retrieve')
    """)
    op.execute("""
        UPDATE chat_turns SET rewrite_provider_id='sarvam', rewrite_model_id=model_id
        WHERE model_id IS NOT NULL AND state='completed' AND rewrite_prompt_version IS NOT NULL
    """)
    op.execute("""
        ALTER TABLE reviews ADD CONSTRAINT reviews_provider_valid
        CHECK (provider_id IS NULL OR provider_id IN ('sarvam','indierouter'))
    """)
    op.execute("""
        ALTER TABLE chat_turns ADD CONSTRAINT chat_turns_provider_valid
        CHECK (provider_id IS NULL OR provider_id IN ('sarvam','indierouter'))
    """)
    op.execute("""
        ALTER TABLE chat_turns ADD CONSTRAINT chat_turns_rewrite_provider_valid
        CHECK (rewrite_provider_id IS NULL OR rewrite_provider_id IN ('sarvam','indierouter'))
    """)


def downgrade() -> None:
    raise RuntimeError("Provider provenance is historical audit data; use a forward repair migration")
