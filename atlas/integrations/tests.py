"""Integration Layer tests.

Run: python3 -m atlas.integrations.tests
All tests use mocks/temp dirs — no real Gmail/iMessage/Calendar access needed.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    print("Integration Layer Test Suite")
    print("=" * 60)

    # ── Test 1: BaseIntegration health state ────────────────────────────
    print("\n[1] BaseIntegration Health State")

    from atlas.integrations.base import BaseIntegration, IntegrationHealth

    class DummyIntegration(BaseIntegration):
        name = "dummy"
        async def poll(self) -> list[dict]:
            return []
        def health_check(self) -> IntegrationHealth:
            return self._health

    dummy = DummyIntegration()
    check("starts in 'down' state", dummy.health_check().status == "down")

    dummy._ok({"detail": "test"})
    check("_ok() sets healthy", dummy.health_check().status == "healthy")
    check("_ok() sets last_success", dummy.health_check().last_success > 0)

    dummy._fail("connection refused")
    check("_fail() after success sets degraded", dummy.health_check().status == "degraded")
    check("_fail() stores error", dummy.health_check().error == "connection refused")

    dummy2 = DummyIntegration()
    dummy2._fail("never worked")
    check("_fail() before any success → down", dummy2.health_check().status == "down")

    health_dict = dummy.health_check().to_dict()
    check("to_dict() has required keys",
          all(k in health_dict for k in ("name", "status", "error", "details")))

    # ── Test 2: iMessage cursor and parsing ─────────────────────────────
    print("\n[2] iMessage Integration")

    from atlas.integrations.imessage import (
        IMessageIntegration, _apple_ts_to_unix, _unix_to_apple_ts
    )

    # Timestamp roundtrip
    unix_now = time.time()
    apple_ts = _unix_to_apple_ts(unix_now)
    unix_back = _apple_ts_to_unix(apple_ts)
    check("apple_ts ↔ unix_ts roundtrip within 1s", abs(unix_back - unix_now) < 1.0,
          f"diff={abs(unix_back - unix_now):.3f}")

    # Nanosecond (pre-BigSur) format: value should be >> 1e12 (detection threshold)
    apple_ns = apple_ts  # _unix_to_apple_ts returns nanoseconds
    check("nanosecond timestamp detected", apple_ns > 1_000_000_000_000,
          f"got {apple_ns}")

    with tempfile.TemporaryDirectory() as tmpdir:
        imsg = IMessageIntegration(
            chat_db_path=Path("/nonexistent/chat.db"),
            data_dir=Path(tmpdir),
        )
        check("missing chat.db → down status", imsg.health_check().status == "down",
              imsg.health_check().status)
        check("missing chat.db → poll returns []",
              await imsg.poll() == [])

        # Test cursor persistence
        imsg2 = IMessageIntegration(
            chat_db_path=Path("/nonexistent/chat.db"),
            data_dir=Path(tmpdir),
        )
        # cursor should be set to ~24h ago on first run (no file yet)
        check("cursor defaults to ~24h ago",
              imsg2._last_apple_ts < _unix_to_apple_ts(time.time()),
              f"cursor={imsg2._last_apple_ts}")

    # ── Test 3: Apple Health XML parsing ────────────────────────────────
    print("\n[3] Apple Health Integration")

    from atlas.integrations.health import AppleHealthIntegration, _parse_date

    # Test date parsing
    date_str = "2026-04-18 10:30:00 +0000"
    ts = _parse_date(date_str)
    check("_parse_date returns float", isinstance(ts, float))
    check("_parse_date is non-zero", ts > 0, f"got {ts}")

    bad_ts = _parse_date("not a date")
    check("_parse_date returns 0 on invalid", bad_ts == 0.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        health = AppleHealthIntegration(data_dir=data_dir)

        # Missing export → down
        check("missing export → down", health.health_check().status == "down")
        events = await health.poll()
        check("missing export → poll returns []", events == [])

        # Create minimal Apple Health XML
        export_dir = data_dir / "apple_health_export"
        export_dir.mkdir()
        xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
  <Record type="HKQuantityTypeIdentifierRestingHeartRate"
          unit="count/min"
          value="65"
          startDate="2026-04-17 08:00:00 +0000"
          endDate="2026-04-17 08:00:00 +0000"/>
  <Record type="HKQuantityTypeIdentifierRestingHeartRate"
          unit="count/min"
          value="95"
          startDate="2026-04-18 08:00:00 +0000"
          endDate="2026-04-18 08:00:00 +0000"/>
  <Record type="HKCategoryTypeIdentifierSleepAnalysis"
          value="HKCategoryValueSleepAnalysisAsleep"
          startDate="2026-04-18 01:00:00 +0000"
          endDate="2026-04-18 05:00:00 +0000"/>
</HealthData>'''
        (export_dir / "export.xml").write_text(xml_content)

        # Set cursor far in past so all records count as "new"
        health._last_ts = 0.0

        events = await health.poll()
        types = [e.get("type") for e in events]
        metrics = [e.get("metric") for e in events]

        check("parsed health_metric events", "health_metric" in types,
              f"types={types}")
        check("resting_heart_rate metric present", "resting_heart_rate" in metrics,
              f"metrics={metrics}")
        check("sleep_hours metric present", "sleep_hours" in metrics,
              f"metrics={metrics}")

        # Check anomaly detection (HR=95 > 90 threshold)
        check("elevated HR anomaly detected",
              any(e.get("type") == "health_anomaly" for e in events),
              f"events={[(e.get('type'), e.get('metric')) for e in events]}")

        check("sleep hours ~4h",
              any(abs(e.get("value", 0) - 4.0) < 0.1
                  for e in events if e.get("metric") == "sleep_hours"),
              f"sleep events={[e for e in events if e.get('metric') == 'sleep_hours']}")

        check("local_only marker present",
              all(e.get("_local_only") for e in events if "health" in e.get("type", "")))

        # cursor updated
        check("cursor advanced after poll", health._last_ts > 0)

        # Second poll with same cursor → no duplicate events
        events2 = await health.poll()
        health_events2 = [e for e in events2 if e.get("type") == "health_metric"]
        check("second poll returns no duplicates", len(health_events2) == 0,
              f"got {len(health_events2)}")

    # ── Test 4: Calendar date parsing ───────────────────────────────────
    print("\n[4] Calendar Date Parsing")

    from atlas.integrations.calendar import _parse_applescript_date, _parse_calendar_output

    # Various AppleScript date formats
    date1 = "Friday, April 18, 2026 at 10:00:00 AM"
    ts1 = _parse_applescript_date(date1)
    check("AppleScript long date parses", ts1 is not None, f"got {ts1}")

    date2 = "04/18/2026, 10:00 AM"
    ts2 = _parse_applescript_date(date2)
    check("MM/DD/YYYY date parses", ts2 is not None, f"got {ts2}")

    date3 = "2026-04-18 10:00:00"
    ts3 = _parse_applescript_date(date3)
    check("ISO date parses", ts3 is not None, f"got {ts3}")

    bad_date = _parse_applescript_date("not a date at all")
    check("bad date returns None", bad_date is None)

    # Test calendar output parsing
    raw = "Sprint Planning|Friday, April 18, 2026 at 10:00:00 AM\nLunch|Friday, April 18, 2026 at 12:00:00 PM\n"
    parsed = _parse_calendar_output(raw)
    check("calendar output parsed", len(parsed) == 2, f"got {len(parsed)}")
    check("event titles extracted", parsed[0]["title"] == "Sprint Planning")

    # Empty output
    empty = _parse_calendar_output("")
    check("empty output returns []", empty == [])

    # ── Test 5: CalendarIntegration with mocked AppleScript ─────────────
    print("\n[5] CalendarIntegration (mocked AppleScript)")

    from atlas.integrations.calendar import CalendarIntegration, MEETING_NOW_WINDOW

    cal = CalendarIntegration()

    # Mock AppleScript to return an event starting in 2 minutes
    now = time.time()
    start_in_2min = now + 2 * 60
    from datetime import datetime as dt
    start_str = dt.fromtimestamp(start_in_2min).strftime("%Y-%m-%d %H:%M:%S")
    mock_raw = f"Daily Standup|{start_str}\n"

    with patch("atlas.integrations.calendar.AppleScriptBackend") as MockBackend:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=(True, mock_raw, {}))
        MockBackend.return_value = mock_instance

        events = await cal.poll()

    check("calendar emits meeting_soon event", len(events) >= 1, f"events={events}")
    if events:
        check("event type is meeting_soon or meeting_now",
              events[0]["event_type"] in ("meeting_soon", "meeting_now"),
              f"got {events[0]['event_type']}")
        check("event has title", "Daily Standup" in events[0].get("title", ""))

    # Test deduplication — same event should not be emitted twice
    with patch("atlas.integrations.calendar.AppleScriptBackend") as MockBackend:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=(True, mock_raw, {}))
        MockBackend.return_value = mock_instance

        events2 = await cal.poll()
    check("duplicate event suppressed on second poll", len(events2) == 0,
          f"got {len(events2)} events")

    # AppleScript failure → health degraded
    cal2 = CalendarIntegration()
    with patch("atlas.integrations.calendar.AppleScriptBackend") as MockBackend:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock(return_value=(False, "osascript timeout", {}))
        MockBackend.return_value = mock_instance

        events3 = await cal2.poll()
    check("AppleScript failure → empty events", events3 == [])
    check("AppleScript failure → degraded health",
          cal2.health_check().status in ("degraded", "down"),
          f"got {cal2.health_check().status}")

    # ── Test 6: IntegrationManager ───────────────────────────────────────
    print("\n[6] IntegrationManager")

    from atlas.integrations.manager import IntegrationManager

    with tempfile.TemporaryDirectory() as tmpdir:
        received: list[dict] = []

        def capture(events: list[dict]) -> None:
            received.extend(events)

        manager = IntegrationManager(data_dir=Path(tmpdir), event_callback=capture)

        check("health before start → manager down",
              manager.health_check()["status"] == "down")

        # Register a mock integration
        class FastIntegration(BaseIntegration):
            name = "fast"
            _count = 0
            async def poll(self) -> list[dict]:
                self._count += 1
                self._ok()
                return [{"type": "test_event", "n": self._count}]
            def health_check(self) -> IntegrationHealth:
                return self._health

        fast = FastIntegration()
        manager.register(fast, poll_interval=0.01)  # poll immediately

        await manager.start()
        check("manager healthy after start", manager.health_check()["status"] == "healthy")

        # Force poll all
        events = await manager.poll_all_now()
        check("poll_all_now returns events", len(events) > 0, f"got {len(events)}")
        check("event type correct", events[0].get("type") == "test_event")

        await manager.stop()
        check("manager stopped", not manager._running)

        # health_check includes integration status
        health = manager.health_check()
        check("health includes integrations key", "integrations" in health)
        check("fast integration in health", "fast" in health["integrations"])

    # ── Test 7: build_default factory ───────────────────────────────────
    print("\n[7] IntegrationManager.build_default()")

    with tempfile.TemporaryDirectory() as tmpdir:
        manager = IntegrationManager.build_default(
            data_dir=Path(tmpdir),
            enable_gmail=False,   # skip — needs credentials
            enable_imessage=True,
            enable_health=False,  # skip — needs export
            enable_calendar=True,
        )
        check("build_default creates manager", manager is not None)
        check("imessage registered", "imessage" in manager._integrations)
        check("calendar registered", "calendar" in manager._integrations)
        check("gmail not registered (disabled)", "gmail" not in manager._integrations)

    print("\n" + "=" * 60)
    total = _PASS + _FAIL
    print(f"Results: {_PASS}/{total} passed" + (f"  ({_FAIL} FAILED)" if _FAIL else "  (all pass)"))
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
