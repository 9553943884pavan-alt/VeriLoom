"""Immutable revision/review records, evidence, follow-ups, and human decisions."""

from alembic import op

revision = "004_review"
down_revision = "003_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        CREATE TABLE drafts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organisations(id) ON DELETE RESTRICT,
            creator_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            title text NOT NULL,
            channel text NOT NULL,
            content_format text NOT NULL DEFAULT 'text',
            working_content text NOT NULL,
            working_copy_version bigint NOT NULL DEFAULT 1,
            state text NOT NULL DEFAULT 'editing',
            current_revision_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            CONSTRAINT drafts_copy_version_valid CHECK (working_copy_version >= 1),
            CONSTRAINT drafts_text_only CHECK (content_format = 'text'),
            CONSTRAINT drafts_state_valid CHECK (state IN
                ('editing','processing','ready_for_review','review_failed','changes_requested','approved'))
        );
        CREATE INDEX drafts_creator_inbox_idx ON drafts(org_id, creator_user_id, updated_at DESC);
        CREATE INDEX drafts_state_inbox_idx ON drafts(org_id, state, updated_at DESC);
        CREATE TABLE draft_revisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            draft_id uuid NOT NULL,
            revision_number integer NOT NULL,
            title text NOT NULL,
            channel text NOT NULL,
            content_format text NOT NULL,
            content_nfc text NOT NULL,
            content_sha256 char(64) NOT NULL,
            submitted_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            submitted_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            UNIQUE (org_id, draft_id, id),
            UNIQUE (draft_id, revision_number),
            FOREIGN KEY (org_id, draft_id) REFERENCES drafts(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT draft_revisions_number_valid CHECK (revision_number >= 1),
            CONSTRAINT draft_revisions_text_only CHECK (content_format = 'text')
        );
        CREATE INDEX draft_revisions_latest_idx ON draft_revisions(draft_id, revision_number DESC);
        ALTER TABLE drafts ADD CONSTRAINT drafts_current_revision_fk
            FOREIGN KEY (org_id, id, current_revision_id)
            REFERENCES draft_revisions(org_id, draft_id, id) ON DELETE RESTRICT;
        CREATE TABLE reviews (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            revision_id uuid NOT NULL UNIQUE,
            reviewer_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            state text NOT NULL DEFAULT 'queued',
            review_version bigint NOT NULL DEFAULT 1,
            reviewed_against_corpus_version bigint,
            evidence_as_of_date date,
            coverage text,
            coverage_reason text,
            prompt_version text,
            model_id text,
            validator_outcome text,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            UNIQUE (org_id, id, revision_id),
            FOREIGN KEY (org_id, revision_id) REFERENCES draft_revisions(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT reviews_state_valid CHECK (state IN
                ('queued','processing','ready_for_review','failed','changes_requested','approved')),
            CONSTRAINT reviews_version_valid CHECK (review_version >= 1),
            CONSTRAINT reviews_coverage_valid CHECK (coverage IS NULL OR coverage IN
                ('sufficient','partial','insufficient')),
            CONSTRAINT reviews_coverage_reason_valid CHECK (
                (coverage = 'sufficient' AND coverage_reason IS NULL)
                OR (coverage IS NULL AND coverage_reason IS NULL)
                OR (coverage IN ('partial','insufficient') AND nullif(btrim(coverage_reason),'') IS NOT NULL))
        );
        CREATE INDEX reviews_reviewer_inbox_idx ON reviews(org_id, reviewer_user_id, state, created_at DESC);
        CREATE TABLE review_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            review_id uuid NOT NULL,
            passage_id uuid NOT NULL,
            rank integer NOT NULL,
            supplied_text text NOT NULL,
            source_version text NOT NULL,
            retrieval_scores jsonb,
            UNIQUE (org_id, id),
            UNIQUE (review_id, passage_id),
            FOREIGN KEY (org_id, review_id) REFERENCES reviews(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, passage_id) REFERENCES source_passages(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT review_evidence_rank_valid CHECK (rank >= 1)
        );
        CREATE INDEX review_evidence_rank_idx ON review_evidence(review_id, rank);
        CREATE TABLE findings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            review_id uuid NOT NULL,
            category text NOT NULL,
            severity text NOT NULL,
            evidence_status text NOT NULL,
            quote text,
            anchor_start integer,
            anchor_end integer,
            explanation text NOT NULL,
            suggested_edit text,
            sort_order integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            UNIQUE (org_id, review_id, id),
            FOREIGN KEY (org_id, review_id) REFERENCES reviews(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT findings_category_valid CHECK (category IN
                ('tone','terminology','factual_claim','promotional_claim','accessibility','visual_asset','other')),
            CONSTRAINT findings_severity_valid CHECK (severity IN ('low','medium','high')),
            CONSTRAINT findings_evidence_status_valid CHECK (evidence_status IN ('supported','insufficient')),
            CONSTRAINT findings_anchor_valid CHECK (
                (anchor_start IS NULL AND anchor_end IS NULL)
                OR (anchor_start IS NOT NULL AND anchor_end IS NOT NULL
                    AND anchor_start >= 0 AND anchor_end > anchor_start)),
            CONSTRAINT findings_sort_order_valid CHECK (sort_order >= 0)
        );
        CREATE INDEX findings_review_sort_idx ON findings(review_id, sort_order);
        CREATE TABLE finding_citations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            passage_id uuid NOT NULL,
            source_ref text NOT NULL,
            source_version text NOT NULL,
            source_title text NOT NULL,
            excerpt text NOT NULL,
            effective_from date NOT NULL,
            sort_order integer NOT NULL,
            UNIQUE (org_id, id),
            UNIQUE (finding_id, passage_id),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, passage_id) REFERENCES source_passages(org_id, id) ON DELETE RESTRICT
        );
        CREATE TABLE chat_turns (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            review_id uuid NOT NULL,
            actor_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            finding_id uuid,
            question text NOT NULL,
            rewritten_query text,
            route text,
            answer text,
            state text NOT NULL DEFAULT 'queued',
            answer_status text,
            reviewed_against_corpus_version bigint,
            evidence_as_of_date date,
            router_version text,
            rewrite_prompt_version text,
            answer_prompt_version text,
            model_id text,
            validator_outcome text,
            error_code text,
            created_at timestamptz NOT NULL DEFAULT now(),
            answered_at timestamptz,
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, review_id) REFERENCES reviews(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, review_id, finding_id)
                REFERENCES findings(org_id, review_id, id) ON DELETE RESTRICT,
            CONSTRAINT chat_turns_state_valid CHECK (state IN ('queued','processing','completed','failed')),
            CONSTRAINT chat_turns_answer_status_valid CHECK (
                answer_status IS NULL OR answer_status IN ('answered','clarification_needed','insufficient_evidence')),
            CONSTRAINT chat_turns_route_valid CHECK (
                route IS NULL OR route IN ('reuse_evidence','rewrite_retrieve','fresh_retrieve','clarify','abstain')),
            CONSTRAINT chat_turns_completed_answer CHECK (
                state <> 'completed' OR (answer IS NOT NULL AND answer_status IS NOT NULL))
        );
        CREATE INDEX chat_turns_review_history_idx ON chat_turns(review_id, created_at, id);
        CREATE TABLE chat_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            turn_id uuid NOT NULL,
            passage_id uuid NOT NULL,
            rank integer NOT NULL,
            supplied_text text NOT NULL,
            source_version text NOT NULL,
            retrieval_scores jsonb,
            UNIQUE (org_id, id),
            UNIQUE (turn_id, passage_id),
            FOREIGN KEY (org_id, turn_id) REFERENCES chat_turns(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, passage_id) REFERENCES source_passages(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT chat_evidence_rank_valid CHECK (rank >= 1)
        );
        CREATE INDEX chat_evidence_rank_idx ON chat_evidence(turn_id, rank);
        CREATE TABLE chat_citations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            turn_id uuid NOT NULL,
            passage_id uuid NOT NULL,
            source_ref text NOT NULL,
            source_version text NOT NULL,
            source_title text NOT NULL,
            excerpt text NOT NULL,
            effective_from date NOT NULL,
            sort_order integer NOT NULL,
            UNIQUE (org_id, id),
            UNIQUE (turn_id, passage_id),
            FOREIGN KEY (org_id, turn_id) REFERENCES chat_turns(org_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (org_id, passage_id) REFERENCES source_passages(org_id, id) ON DELETE RESTRICT
        );
        CREATE TABLE comments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            review_id uuid NOT NULL,
            revision_id uuid NOT NULL,
            actor_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            body text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, review_id, revision_id)
                REFERENCES reviews(org_id, id, revision_id) ON DELETE RESTRICT,
            CONSTRAINT comments_body_nonempty CHECK (nullif(btrim(body),'') IS NOT NULL)
        );
        CREATE INDEX comments_review_history_idx ON comments(org_id, review_id, created_at, id);
        CREATE TABLE decisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            review_id uuid NOT NULL UNIQUE,
            actor_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            action text NOT NULL,
            comment text,
            override_reason text,
            expected_review_version bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, review_id) REFERENCES reviews(org_id, id) ON DELETE RESTRICT,
            CONSTRAINT decisions_action_valid CHECK (action IN ('request_changes','approve')),
            CONSTRAINT decisions_comment_required CHECK (
                action <> 'request_changes' OR nullif(btrim(comment),'') IS NOT NULL),
            CONSTRAINT decisions_version_valid CHECK (expected_review_version >= 1)
        );
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Review history is append-only; use a forward repair migration")
