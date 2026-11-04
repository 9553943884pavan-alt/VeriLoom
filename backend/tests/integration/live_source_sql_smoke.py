"""Opt-in live SQL check; creates, migrates and drops its own disposable DB.

Run from backend with ``python -m tests.integration.live_source_sql_smoke``.
Uses the configured server credentials without writing to the application DB.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.repositories import drafts, evidence, reviews, sources


def work(engine) -> tuple:
    org, other, actor, source, first, second, passage = (uuid4() for _ in range(7))
    with Session(engine) as db, db.begin():
        today = db.execute(text("SELECT timezone('UTC', now())::date")).scalar_one()
        db.execute(text("INSERT INTO users (id,email,display_name,password_hash) "
                        "VALUES (:id,'sql-test@example.com','SQL test','unused')"), {"id": actor})
        for oid in (org, other):
            db.execute(text("INSERT INTO organisations (id,slug,name,time_zone) "
                            "VALUES (:id,:slug,'SQL test','UTC')"), {"id": oid, "slug": str(oid)})
        db.execute(text("INSERT INTO sources (id,org_id,title,category) "
                        "VALUES (:id,:org,'Original','guidelines')"), {"id": source, "org": org})
        db.execute(text("INSERT INTO source_versions "
                        "(id,org_id,source_id,version_label,origin_path,sha256,effective_from,"
                        "ingestion_status,approval_status) VALUES "
                        "(:id,:org,:source,'1.0','synthetic-test',:sha,:day,'ready','approved')"),
                   {"id": first, "org": org, "source": source, "sha": "0" * 64, "day": today})
    return org, other, actor, source, first, second, passage


def exercise(engine, ids) -> None:
    org, other, actor, source, first, second, passage = ids
    with Session(engine) as db, db.begin():
        today = db.execute(text("SELECT timezone('UTC', now())::date")).scalar_one()
        metadata = db.execute(text("SELECT title,category FROM source_versions WHERE id=:id"),
                              {"id": first}).one()
        assert tuple(metadata) == ("Original", "guidelines")
        db.execute(text("INSERT INTO source_passages "
                        "(id,org_id,source_id,version_id,source_ref,chunk_index,title,text_content,"
                        "start_offset,end_offset,category,embedding,embedding_model) VALUES "
                        "(:id,:org,:source,:version,'test:1',0,'Original','Original guidance',"
                        "0,17,'guidelines',CAST(:vector AS vector),'synthetic-test')"),
                   {"id": passage, "org": org, "source": source, "version": first,
                    "vector": "[" + ",".join(["0"] * 384) + "]"})
        assert sources.lock_source_for_replacement(db, org_id=org, source_id=source)["major"] == 1
        sources.insert_replacement_version(
            db, org_id=org, source_id=source, version_id=second, actor_id=actor,
            version_label="2.0", title="Replacement", category="facts", object_key=str(uuid4()),
            sha256="1" * 64, mime_type="text/plain", byte_size=10,
            effective_from=today, effective_to=None,
        )
        assert sources.get_source_version(db, org_id=org, source_id=source,
                                          version_id=second)["title"] == "Replacement"
        assert sources.get_source_version(db, org_id=other, source_id=source,
                                          version_id=second) is None
        assert sources.list_sources(db, org_id=org, status="active", after=None, limit=10)[0]["title"] == "Original"
        assert evidence.get_passage(db, org_id=org, passage_id=passage)["current_status"] == "approved"
        assert evidence.get_passage(db, org_id=other, passage_id=passage) is None
        assert not evidence.user_may_read_cited_passage(
            db, org_id=org, passage_id=passage, user_id=actor, is_creator=True, is_reviewer=True)
        db.execute(text("UPDATE source_versions SET ingestion_status='ready' WHERE id=:id"), {"id": second})
        sources.activate_source_version(db, org_id=org, source_id=source, version_id=second, corpus_version=1)
        assert sources.list_sources(db, org_id=org, status="active", after=None, limit=10)[0]["title"] == "Replacement"
        historical = evidence.get_passage(db, org_id=org, passage_id=passage)
        assert historical["current_status"] == "archived" and historical["excerpt"] == "Original guidance"
        sources.archive_source(db, org_id=org, source_id=source, corpus_version=2)
        assert sources.list_sources(db, org_id=org, status="active", after=None, limit=10) == []
        assert len(sources.list_sources(db, org_id=org, status="archived", after=None, limit=10)) == 1
        assert evidence.get_passage(db, org_id=org, passage_id=passage)["current_status"] == "archived"
        assert db.execute(text("SELECT corpus_version FROM organisations WHERE id=:id"), {"id": org}).scalar_one() == 2
    print("PASS metadata backfill, replacement, activation, archival, historical evidence and tenant isolation")


def seed_provider_history(engine, ids) -> list[tuple]:
    org, _, actor, _, _, _, passage = ids
    cases = []
    with Session(engine) as db, db.begin():
        for state, has_evidence in (("ready_for_review", True), ("failed", True),
                                    ("ready_for_review", False), ("processing", False)):
            draft, revision, review = (uuid4() for _ in range(3))
            params = {"org": org, "actor": actor, "draft": draft, "revision": revision,
                      "review": review, "state": state, "sha": "0" * 64, "passage": passage}
            db.execute(text("INSERT INTO drafts (id,org_id,creator_user_id,title,channel,working_content) "
                            "VALUES (:draft,:org,:actor,'Test','social','Test')"), params)
            db.execute(text("INSERT INTO draft_revisions (id,org_id,draft_id,revision_number,title,channel,"
                            "content_format,content_nfc,content_sha256,submitted_by_user_id) "
                            "VALUES (:revision,:org,:draft,1,'Test','social','text','Test',:sha,:actor)"), params)
            db.execute(text("UPDATE drafts SET current_revision_id=:revision WHERE id=:draft"), params)
            db.execute(text("INSERT INTO reviews (id,org_id,revision_id,reviewer_user_id,state,model_id) "
                            "VALUES (:review,:org,:revision,:actor,:state,'historical-model')"), params)
            if has_evidence:
                db.execute(text("INSERT INTO review_evidence (org_id,review_id,passage_id,rank,supplied_text,source_version) "
                                "VALUES (:org,:review,:passage,1,'Original guidance','1.0')"), params)
            turns = []
            for route, completed, rewrite in (("fresh_retrieve", True, False),
                                               ("rewrite_retrieve", True, True),
                                               ("clarify", True, False),
                                               ("fresh_retrieve", False, True)):
                turn = uuid4()
                db.execute(text("INSERT INTO chat_turns (id,org_id,review_id,actor_user_id,question,route,state,"
                                "answer,answer_status,model_id,rewrite_prompt_version) VALUES "
                                "(:turn,:org,:review,:actor,'Test',:route,:state,'Test','answered',"
                                "'historical-model',:rewrite)"),
                           {**params, "turn": turn, "route": route,
                            "state": "completed" if completed else "failed",
                            "rewrite": "v1" if rewrite else None})
                turns.append(turn)
            cases.append((review, revision, turns))
    return cases


def exercise_provider_attribution(engine, ids, cases) -> None:
    from app.jobs.handlers.answer_question import _publish as publish_answer
    from app.jobs.handlers.review_revision import _publish as publish_review

    org = ids[0]
    with Session(engine) as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "007_provider_attribution"
        for index, (review, _, turns) in enumerate(cases):
            assert db.execute(text("SELECT provider_id FROM reviews WHERE id=:id"),
                              {"id": review}).scalar_one() == ("sarvam" if index == 0 else None)
            for number, turn in enumerate(turns):
                row = db.execute(text("SELECT provider_id,rewrite_provider_id,rewrite_model_id "
                                      "FROM chat_turns WHERE id=:id"), {"id": turn}).one()
                assert tuple(row) == (
                    "sarvam" if number in (0, 1) else None,
                    "sarvam" if number == 1 else None,
                    "historical-model" if number == 1 else None,
                )
        for statement in ("UPDATE reviews SET provider_id='invalid'",
                          "UPDATE chat_turns SET provider_id='invalid'",
                          "UPDATE chat_turns SET rewrite_provider_id='invalid'"):
            try:
                with db.begin_nested():
                    db.execute(text(statement))
            except IntegrityError:
                pass
            else:
                raise AssertionError("Provider constraint accepted an unknown provider")
        db.rollback()
        review, revision, turns = cases[-1]
        publish_review(db, org_id=org, review_id=review, revision_id=revision,
                       draft="Test", snapshot=[], validated=SimpleNamespace(
                           findings=[], coverage="insufficient", coverage_reason="Synthetic test"),
                       provider_id="indierouter", model_id="fallback-model")
        turn = turns[-1]
        db.execute(text("UPDATE chat_turns SET state='processing',rewrite_provider_id='sarvam',"
                        "rewrite_model_id='rewrite-model' WHERE id=:id"), {"id": turn})
        db.commit()
        publish_answer(db, org_id=org, turn_id=turn, turn={"revision_id": revision},
                       passages=[], route="fresh_retrieve", answer_status="answered",
                       answer="Synthetic test", citations=[], provider_id="indierouter", model_id="answer-model")
        assert tuple(db.execute(text("SELECT provider_id,model_id FROM reviews WHERE id=:id"),
                                {"id": review}).one()) == ("indierouter", "fallback-model")
        assert tuple(db.execute(text("SELECT provider_id,model_id,rewrite_provider_id,rewrite_model_id "
                                     "FROM chat_turns WHERE id=:id"), {"id": turn}).one()) == (
                                         "indierouter", "answer-model", "sarvam", "rewrite-model")
    print("PASS migration 007 conservative backfills, provider constraints and review/chat publish SQL")
    exercise_nullable_cursors(engine, ids, cases)


def exercise_nullable_cursors(engine, ids, cases) -> None:
    org, _, actor, *_ = ids
    review, revision, _ = cases[-1]
    with Session(engine) as db, db.begin():
        items = drafts.list_drafts(db, org_id=org, user_id=actor, view="mine",
                                   state=None, after=None, limit=20)
        assert len(items) == len(cases)
        assert drafts.list_drafts(db, org_id=org, user_id=actor, view="mine",
                                  state="editing", after=items[-1]["draft_id"], limit=20) == []
        draft_id = db.execute(text("SELECT draft_id FROM draft_revisions WHERE id=:id"),
                              {"id": revision}).scalar_one()
        assert len(drafts.list_revisions(db, org_id=org, draft_id=draft_id, before=None, limit=20)) == 1
        assert drafts.list_revisions(db, org_id=org, draft_id=draft_id, before=1, limit=20) == []
        assert reviews.list_comments(db, org_id=org, review_id=review, after=None, limit=20) == []
        comment = reviews.insert_comment(db, org_id=org, review_id=review, revision_id=revision,
                                         actor_id=actor, body="Synthetic cursor regression")
        assert len(reviews.list_comments(db, org_id=org, review_id=review, after=None, limit=20)) == 1
        assert reviews.list_comments(db, org_id=org, review_id=review,
                                     after=(comment["created_at"], comment["comment_id"]), limit=20) == []
        questions = reviews.list_questions(db, org_id=org, review_id=review, after=None, limit=20)
        assert len(questions) == 4
        assert reviews.list_questions(db, org_id=org, review_id=review,
                                      after=(questions[-1]["created_at"], questions[-1]["turn_id"]), limit=20) == []
    print("PASS PostgreSQL nullable and typed draft/revision/comment/question cursor queries")


def main() -> None:
    configured = make_url(get_settings().DATABASE_URL.get_secret_value())
    configured = configured.set(drivername="postgresql+psycopg")
    name = "veriloom_live_test_" + uuid4().hex
    assert name.startswith("veriloom_live_test_") and name != configured.database
    admin = create_engine(configured.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 10}, hide_parameters=True)
    target = configured.set(database=name)
    engine = None
    created = False
    previous = os.environ.get("DATABASE_URL")
    try:
        with admin.connect() as connection:
            connection.connection.driver_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        print("PASS created separate disposable database", flush=True)
        os.environ["DATABASE_URL"] = target.render_as_string(hide_password=False)
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
        command.upgrade(config, "005_jobs")
        engine = create_engine(target, hide_parameters=True)
        ids = work(engine)
        command.upgrade(config, "006_source_version_metadata")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "006_source_version_metadata"
        exercise(engine, ids)
        provider_ids = seed_provider_history(engine, ids)
        command.upgrade(config, "007_provider_attribution")
        exercise_provider_attribution(engine, ids, provider_ids)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.connection.driver_connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                assert not connection.execute(text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}).first()
            print("PASS disposable database dropped; absence confirmed", flush=True)
        admin.dispose()


if __name__ == "__main__":
    main()
