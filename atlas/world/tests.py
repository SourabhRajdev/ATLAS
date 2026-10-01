"""World Model tests — entity CRUD, conflict resolution, context assembly, decay.

Run: python3 -m atlas.world.tests
No API keys required. All tests use a temp SQLite database.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f" | {detail}" if detail else ""))


async def run_tests() -> None:
    print("=" * 60)
    print("World Model Test Suite")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "world.db"

        from atlas.world.world_model import WorldModel
        from atlas.world.models import WorldEvent, EntityType
        from atlas.world.extractor import extract_from_email, extract_from_git_commit

        world = WorldModel(db_path)

        # ── Test 1: Basic entity upsert and retrieval ──────────────────
        print("\n[1] Entity Upsert and Retrieval")

        e1 = await world.upsert_entity("Person", "Priya Sharma", "gmail")
        check("entity created", e1.id != "")
        check("entity name correct", e1.name == "Priya Sharma")
        check("entity type correct", e1.type == "Person")
        check("entity canonical_name lowercase", e1.canonical_name == "priya sharma")

        # Retrieve by name
        found = await world.get_entity("Priya Sharma")
        check("get_entity by name", found is not None and found.id == e1.id)

        # Retrieve by ID
        found_by_id = await world.get_entity(e1.id)
        check("get_entity by id", found_by_id is not None and found_by_id.id == e1.id)

        # ── Test 2: Deduplication ────────────────────────────────────────
        print("\n[2] Entity Deduplication (85% threshold)")

        e2 = await world.upsert_entity("Person", "priya sharma", "imessage")
        check("same person deduped (different case)", e2.id == e1.id, f"got {e2.id} vs {e1.id}")

        e3 = await world.upsert_entity("Person", "Priya S.", "imessage")
        # "priya s." vs "priya sharma" — similarity should be < 0.85 → new entity
        # (this tests the threshold properly)
        is_new = e3.id != e1.id
        check("different-enough name creates new entity", is_new, f"got id={e3.id}")

        # ── Test 3: Attribute conflict resolution ─────────────────────────
        print("\n[3] Attribute Conflict Resolution")

        attr1 = await world.update_attribute(e1.id, "email", "priya@work.com", "gmail")
        check("attribute created", attr1.id > 0)
        check("attribute value correct", attr1.value == "priya@work.com")
        check("attribute is current", attr1.is_current())

        # Same key, same source, different value → supersede
        attr2 = await world.update_attribute(e1.id, "email", "priya@personal.com", "gmail")
        check("new attribute created", attr2.id != attr1.id)

        # Verify old attribute is superseded
        all_attrs = world.get_attributes(e1.id)
        current_emails = [a for a in all_attrs if a.key == "email"]
        check("only one current email attribute", len(current_emails) == 1,
              f"got {len(current_emails)}")
        check("current email is the new one", current_emails[0].value == "priya@personal.com")

        # Different source → both stored (confidence-weighted)
        attr3 = await world.update_attribute(e1.id, "email", "priya@imessage.com", "imessage")
        all_attrs_2 = world.get_attributes(e1.id)
        email_sources = {a.source for a in all_attrs_2 if a.key == "email"}
        check("multiple sources stored", "gmail" in email_sources or "imessage" in email_sources)

        # ── Test 3b: Supersession actually preserves history (regression) ──
        # Test 3 above only ever queries get_attributes(), which filters to
        # current rows — it would pass whether the old value was soft
        # -superseded or hard-deleted. This checks the raw table directly,
        # which is the only way the original bug (UNIQUE(entity_id, key,
        # source) + INSERT OR REPLACE silently deleting the old row instead
        # of superseding it) was actually caught.
        print("\n[3b] Attribute Supersession Preserves History (regression)")

        e2 = await world.upsert_entity("Person", "Sourabh", "user")
        t_before = time.time()
        await world.update_attribute(e2.id, "lives_in", "Vellore", source="user")
        t_mid = time.time()
        await world.update_attribute(e2.id, "lives_in", "Bangalore", source="user")
        t_after = time.time()

        raw_rows = world._conn.execute(
            "SELECT value, superseded_by, valid_to FROM attributes "
            "WHERE entity_id = ? AND key = 'lives_in' ORDER BY id",
            (e2.id,),
        ).fetchall()
        check("both the old and new value exist in the raw table", len(raw_rows) == 2,
              f"got {len(raw_rows)}: {[dict(r) for r in raw_rows]}")
        if len(raw_rows) == 2:
            check("old row (Vellore) is marked superseded, not deleted",
                  raw_rows[0]["value"] == "Vellore" and raw_rows[0]["superseded_by"] is not None)
            check("old row's valid_to is set (closed out)", raw_rows[0]["valid_to"] is not None)
            check("new row (Bangalore) is current (no superseded_by, no valid_to)",
                  raw_rows[1]["value"] == "Bangalore"
                  and raw_rows[1]["superseded_by"] is None
                  and raw_rows[1]["valid_to"] is None)

        current_lives_in = world.get_attributes(e2.id)
        current_val = next((a.value for a in current_lives_in if a.key == "lives_in"), None)
        check("current value is the new one", current_val == "Bangalore")

        at_mid = world.get_attribute_at(e2.id, "lives_in", t_mid)
        check("get_attribute_at(before the move) returns the old value",
              at_mid is not None and at_mid.value == "Vellore",
              f"got {at_mid.value if at_mid else None}")

        at_after = world.get_attribute_at(e2.id, "lives_in", t_after)
        check("get_attribute_at(after the move) returns the new value",
              at_after is not None and at_after.value == "Bangalore",
              f"got {at_after.value if at_after else None}")

        attr_with_evidence = await world.update_attribute(
            e2.id, "preference:summary_style", "bullets", source="user",
            taint="clean", evidence_msg_id="msg-123",
        )
        check("taint round-trips through update_attribute", attr_with_evidence.taint == "clean")
        check("evidence_msg_id round-trips through update_attribute",
              attr_with_evidence.evidence_msg_id == "msg-123")

        # ── Test 3c: migrating a pre-existing old-schema DB ─────────────────
        # Builds a world.db by hand with the OLD schema (table-level
        # UNIQUE(entity_id, key, source), no taint/evidence/valid_to columns)
        # to prove open_db()'s migration actually upgrades a real existing
        # database, not just a fresh one.
        print("\n[3c] Migrating a Pre-Existing Old-Schema Database")

        import sqlite3
        old_db_path = Path(tmpdir) / "old_world.db"
        raw_conn = sqlite3.connect(str(old_db_path))
        raw_conn.executescript("""
            CREATE TABLE entities (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                canonical_name TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
                first_seen REAL NOT NULL, last_updated REAL NOT NULL,
                last_reinforced REAL NOT NULL, source TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}', embedding BLOB
            );
            CREATE TABLE attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL REFERENCES entities(id),
                key TEXT NOT NULL, value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
                recorded_at REAL NOT NULL,
                superseded_by INTEGER REFERENCES attributes(id),
                UNIQUE(entity_id, key, source)
            );
        """)
        raw_conn.execute(
            "INSERT INTO entities (id, type, name, canonical_name, first_seen, "
            "last_updated, last_reinforced, source) VALUES "
            "('e-old', 'Person', 'Old User', 'old user', ?, ?, ?, 'user')",
            (time.time(), time.time(), time.time()),
        )
        raw_conn.execute(
            "INSERT INTO attributes (entity_id, key, value, source, recorded_at) "
            "VALUES ('e-old', 'lives_in', 'Vellore', 'user', ?)",
            (time.time(),),
        )
        raw_conn.commit()
        raw_conn.close()

        from atlas.world.schema import open_db
        migrated_conn = open_db(old_db_path)
        cols = {row["name"] for row in migrated_conn.execute("PRAGMA table_info(attributes)")}
        check("migration adds taint column", "taint" in cols)
        check("migration adds evidence_msg_id column", "evidence_msg_id" in cols)
        check("migration adds valid_to column", "valid_to" in cols)

        index_rows = migrated_conn.execute("PRAGMA index_list(attributes)").fetchall()
        has_old_unique = any(
            r["unique"] and r["origin"] == "u" and "autoindex" in r["name"] for r in index_rows
        )
        check("old table-level UNIQUE autoindex is gone after migration", not has_old_unique,
              f"indexes: {[dict(r) for r in index_rows]}")

        preexisting_row = migrated_conn.execute(
            "SELECT value FROM attributes WHERE entity_id = 'e-old' AND key = 'lives_in'"
        ).fetchone()
        check("pre-existing data survived the migration", preexisting_row is not None
              and preexisting_row["value"] == "Vellore")

        # And the actual bug scenario, replayed against the migrated DB:
        migrated_world = WorldModel(old_db_path)
        await migrated_world.update_attribute("e-old", "lives_in", "Bangalore", source="user")
        migrated_raw = migrated_conn.execute(
            "SELECT value, valid_to FROM attributes WHERE entity_id = 'e-old' "
            "AND key = 'lives_in' ORDER BY id"
        ).fetchall()
        check("post-migration update_attribute still preserves history (not just on a fresh DB)",
              len(migrated_raw) == 2 and migrated_raw[0]["value"] == "Vellore"
              and migrated_raw[1]["value"] == "Bangalore",
              f"got {[dict(r) for r in migrated_raw]}")
        migrated_world.close()

        # ── Test 4: Relationships ───────────────────────────────────────
        print("\n[4] Relationships")

        e4 = await world.upsert_entity("Person", "Ravi Kumar", "gmail")
        await world.link_entities(e1.id, e4.id, "works_with", strength=0.5, source="gmail")

        # Link again → should strengthen
        await world.link_entities(e1.id, e4.id, "works_with", strength=0.5, source="gmail")
        # Check relationship was strengthened (not duplicated)
        import asyncio as _asyncio
        rows = await _asyncio.to_thread(
            world._conn.execute,
            "SELECT COUNT(*) as n, strength FROM relationships WHERE from_entity = ? AND to_entity = ?",
            (e1.id, e4.id),
        )
        row = rows.fetchone()
        check("relationship not duplicated", row["n"] == 1)
        check("relationship strength increased", row["strength"] > 0.5)

        # ── Test 5: WorldEvent ingestion ─────────────────────────────────
        print("\n[5] WorldEvent Ingestion")

        event = WorldEvent(
            event_type="email_received",
            source="gmail",
            payload={
                "sender": "Alice Johnson <alice@company.com>",
                "subject": "Project Atlas meeting tomorrow",
                "body": "Hi, can we meet tomorrow to discuss the Atlas project? "
                        "I'll invite Bob Smith and Carol White too.",
            },
        )
        entities = await world.ingest_event(event)
        check("entities extracted from email", len(entities) > 0, f"got {len(entities)}")
        names = [e.name for e in entities]
        # Alice Johnson should be extracted (high confidence from sender)
        has_alice = any("alice" in n.lower() or "johnson" in n.lower() for n in names)
        check("sender extracted as Person", has_alice, f"names={names}")

        # ── Test 6: Git commit ingestion ────────────────────────────────
        print("\n[6] Git Commit Event Ingestion")

        git_event = WorldEvent(
            event_type="git_commit",
            source="git",
            payload={
                "message": "feat: add world model entity graph",
                "author": "Sourabh Rajdev",
                "repo": "SourabhRajdev/atlas",
            },
        )
        git_entities = await world.ingest_event(git_event)
        check("git entities extracted", len(git_entities) > 0)
        author_found = any("sourabh" in e.name.lower() for e in git_entities)
        check("commit author extracted as Person", author_found, f"entities={[e.name for e in git_entities]}")
        repo_found = any("atlas" in e.name.lower() for e in git_entities)
        check("repo extracted as Project", repo_found)

        # ── Test 7: Context assembly ─────────────────────────────────────
        print("\n[7] Context Assembly")

        from atlas.world.assembler import ContextAssembler, _estimate_tokens
        assembler = ContextAssembler(world)

        context = await assembler.assemble("Priya meeting project", token_budget=500)
        check("context not empty", not context.is_empty())
        check("context has header", "[WORLD CONTEXT]" in context.text)
        check("context has footer", "[END WORLD CONTEXT]" in context.text)
        check("token estimate within budget", context.token_estimate <= 500,
              f"got {context.token_estimate}")

        # Tiny budget → must truncate gracefully, never crash
        tiny_context = await assembler.assemble("anything", token_budget=20)
        check("tiny budget doesn't crash", True)
        check("tiny budget still has structure",
              "[WORLD CONTEXT]" in tiny_context.text and "[END WORLD CONTEXT]" in tiny_context.text)

        # ── Test 8: Confidence decay ─────────────────────────────────────
        print("\n[8] Confidence Decay")

        # Create old entity (manually set last_reinforced to 60 days ago)
        old_entity = await world.upsert_entity("Person", "Old Contact", "web_search")
        old_conf = old_entity.confidence
        await asyncio.to_thread(
            world._conn.execute,
            "UPDATE entities SET last_reinforced = ? WHERE id = ?",
            (time.time() - 61 * 86_400, old_entity.id),
        )
        await asyncio.to_thread(world._conn.commit)

        # Create recent entity
        recent_entity = await world.upsert_entity("Person", "Recent Contact", "gmail")

        # Run decay
        decayed_count = await world.decay_confidence(days_threshold=30.0)
        check("decay ran", decayed_count >= 1, f"decayed {decayed_count}")

        # Old entity should have lower confidence
        old_after = await world.get_entity(old_entity.id)
        check("old entity confidence decayed",
              old_after.confidence < old_conf,
              f"{old_after.confidence} vs {old_conf}")

        # Recent entity should be untouched
        recent_after = await world.get_entity(recent_entity.id)
        check("recent entity not decayed",
              recent_after.confidence >= recent_entity.confidence)

        # ── Test 9: Health check ─────────────────────────────────────────
        print("\n[9] Health Check")

        health = world.health_check()
        check("health status healthy", health["status"] == "healthy")
        check("entity count in health", health["details"]["entity_count"] > 0)

        # ── Test 10: Extractor unit tests ──────────────────────────────
        print("\n[10] Entity Extractor")

        email_mentions = extract_from_email(
            sender="Alice Johnson <alice@company.com>",
            subject="Meeting about Atlas project",
            body="Hi, let's meet tomorrow. Bob Smith will join too.",
        )
        check("email extraction not empty", len(email_mentions) > 0)
        sender_mention = next((m for m in email_mentions if "alice" in m.name.lower()), None)
        check("sender extracted", sender_mention is not None)
        check("sender confidence high", sender_mention.confidence >= 0.7 if sender_mention else False)

        git_mentions = extract_from_git_commit(
            message="feat(payments): add stripe integration",
            author="Sourabh Rajdev",
        )
        author_mention = next((m for m in git_mentions if "sourabh" in m.name.lower()), None)
        check("git author extracted", author_mention is not None)
        check("git author confidence high", author_mention.confidence >= 0.9 if author_mention else False)

        # ── Test 11: Background Fact Extraction (Phase 1 Step D) ────────────
        print("\n[11] Background Fact Extraction (LLM path + regex fallback)")

        from atlas.world.fact_extraction import extract_and_store

        class _FakeLLMQueue:
            """Mirrors LLMQueue.run_job's contract: just runs the job inline."""
            async def run_job(self, job_fn, lane=None, label=None):
                return await job_fn()

        class _FailingLLMQueue:
            async def run_job(self, job_fn, lane=None, label=None):
                raise RuntimeError("simulated background lane failure")

        class _FakeModelRouter:
            def __init__(self, text: str) -> None:
                self._text = text

            async def generate(self, messages, tool_defs, system_prompt):
                from atlas.core.model_router import LLMResponse
                return LLMResponse(text=self._text)

        world_ex = WorldModel(Path(tmpdir) / "world_extraction.db")

        # -- Valid extraction: facts (incl. self-reference), preferences, corrections --
        valid_json = json.dumps({
            "facts": [
                {"subject": "I", "subject_type": "Person", "predicate": "lives_in",
                 "object": "Bangalore", "confidence": 0.9},
                {"subject": "Priya", "subject_type": "Person", "predicate": "role",
                 "object": "manager", "confidence": 0.8},
            ],
            "preferences": [{"slug": "summary_style", "value": "bullets"}],
            "corrections": [{"slug": "tone", "old_value": "formal", "new_value": "casual"}],
        })
        result = await extract_and_store(
            "I live in Bangalore. Priya is my manager. Summarize in bullets, casually.",
            world_ex, _FakeModelRouter(valid_json), _FakeLLMQueue(),
            taint="clean", evidence_msg_id="msg-1",
        )
        check("LLM extraction reports method=llm", result["method"] == "llm")
        check("LLM extraction wrote both facts", result["facts_written"] == 2, f"got {result}")
        check("LLM extraction wrote preference + correction", result["preferences_written"] == 2, f"got {result}")

        user_entity = await world_ex.upsert_entity(type="Person", name="user", source="llm_inference")
        user_attrs = {a.key: a for a in world_ex.get_attributes(user_entity.id)}
        check("self-reference 'I' resolved to the canonical 'user' entity and wrote lives_in",
              "lives_in" in user_attrs and user_attrs["lives_in"].value == "Bangalore")
        check("extracted fact carries the taint it was given", user_attrs["lives_in"].taint == "clean")
        check("extracted fact carries its evidence_msg_id", user_attrs["lives_in"].evidence_msg_id == "msg-1")
        check("preference written under preference:<slug>",
              "preference:summary_style" in user_attrs and user_attrs["preference:summary_style"].value == "bullets")
        check("correction written the same way as a preference",
              "preference:tone" in user_attrs and user_attrs["preference:tone"].value == "casual")

        priya_entity = await world_ex.upsert_entity(type="Person", name="Priya", source="llm_inference")
        priya_attrs = {a.key: a for a in world_ex.get_attributes(priya_entity.id)}
        check("non-self-referential subject resolved to its own entity, not 'user'",
              "role" in priya_attrs and priya_attrs["role"].value == "manager")

        # -- Self-reference normalization: "me" maps to the SAME entity as "I" above --
        me_json = json.dumps({
            "facts": [{"subject": "me", "subject_type": "Person", "predicate": "timezone",
                       "object": "IST", "confidence": 0.9}],
            "preferences": [], "corrections": [],
        })
        await extract_and_store("My timezone is IST.", world_ex, _FakeModelRouter(me_json), _FakeLLMQueue())
        user_attrs_2 = {a.key: a for a in world_ex.get_attributes(user_entity.id)}
        check("'me' normalizes to the same 'user' entity as 'I' did",
              "timezone" in user_attrs_2 and "lives_in" in user_attrs_2,
              "both facts should land on the same entity's attribute list")

        # -- Markdown-fenced JSON still parses --
        fenced_json = "```json\n" + json.dumps({
            "facts": [], "preferences": [{"slug": "fenced_ok", "value": "yes"}], "corrections": [],
        }) + "\n```"
        fenced_result = await extract_and_store(
            "wrap me in fences", world_ex, _FakeModelRouter(fenced_json), _FakeLLMQueue(),
        )
        check("markdown code-fenced JSON is still parsed", fenced_result["method"] == "llm",
              f"got {fenced_result}")

        # -- Invalid JSON falls back to the regex extractor, not an exception --
        # Phrased so the regex extractor's actual heuristics (email addresses,
        # backtick-quoted project names) have something to catch — a bare
        # single-word name like "Priya" alone doesn't trigger its Title-Case
        # heuristic, which requires 2+ consecutive capitalized words.
        invalid_result = await extract_and_store(
            "Got an email from priya@company.com about the `atlas-core` project.",
            world_ex, _FakeModelRouter("I don't have that information."), _FakeLLMQueue(),
        )
        check("non-JSON model output falls back to regex extraction",
              invalid_result["method"] == "regex_fallback", f"got {invalid_result}")
        check("regex fallback still extracts entity mentions",
              invalid_result.get("entities_extracted", 0) > 0, f"got {invalid_result}")

        # -- A failing background lane call also falls back, doesn't raise --
        failure_result = await extract_and_store(
            "Priya mentioned the Atlas rollout again.",
            world_ex, _FakeModelRouter("irrelevant"), _FailingLLMQueue(),
        )
        check("a raising LLMQueue.run_job degrades to regex fallback instead of raising",
              failure_result["method"] == "regex_fallback", f"got {failure_result}")

        world_ex.close()
        world.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
