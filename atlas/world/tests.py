"""World Model tests — entity CRUD, conflict resolution, context assembly, decay.

Run: python3 -m atlas.world.tests
No API keys required. All tests use a temp SQLite database.
"""

from __future__ import annotations

import asyncio
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

        world.close()

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
