"""Apple Health integration — reads exported health data XML.

To export: Health app → profile icon → Export All Health Data → share the zip.
Extract to data_dir/apple_health_export/. This integration parses the XML and
surfaces anomalies (low sleep, elevated resting HR, etc.) as proactive signals.

Privacy: health data NEVER leaves the device. Local processing only.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from atlas.integrations.base import BaseIntegration, IntegrationHealth

logger = logging.getLogger("atlas.integrations.health")

EXPORT_FILENAME = "export.xml"

# Record types we care about
RECORD_TYPES = {
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep",
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_calories",
    "HKQuantityTypeIdentifierBodyMass": "weight",
}

_SLEEP_ASLEEP_VALUE = "HKCategoryValueSleepAnalysisAsleep"

ANOMALY_RULES = [
    ("resting_heart_rate", lambda v: v > 90, "Elevated resting HR: {v:.0f} bpm"),
    ("resting_heart_rate", lambda v: v < 40, "Very low resting HR: {v:.0f} bpm"),
    ("sleep_hours",        lambda v: v < 5,  "Low sleep last night: {v:.1f} hours"),
]


def _parse_date(s: str) -> float:
    """Parse 'YYYY-MM-DD HH:MM:SS ±HHMM' → Unix timestamp."""
    try:
        # Try with timezone
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
        return dt.timestamp()
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return 0.0


class AppleHealthIntegration(BaseIntegration):
    name = "apple_health"

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self._export_dir = data_dir / "apple_health_export"
        self._export_xml = self._export_dir / EXPORT_FILENAME
        self._cursor_path = data_dir / "health_cursor.txt"
        self._last_ts: float = self._load_cursor()

    def _load_cursor(self) -> float:
        if self._cursor_path.exists():
            try:
                return float(self._cursor_path.read_text().strip())
            except Exception:
                pass
        return time.time() - 7 * 86400  # last 7 days on first run

    def _save_cursor(self, ts: float) -> None:
        self._last_ts = ts
        self._cursor_path.write_text(str(ts))

    async def poll(self) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._poll_sync)

    def _poll_sync(self) -> list[dict]:
        if not self._export_xml.exists():
            self._fail(f"Apple Health export not found at {self._export_xml}")
            return []

        events: list[dict] = []
        max_ts = self._last_ts
        daily_sleep: dict[str, float] = {}  # date_str → hours

        try:
            tree = ET.parse(str(self._export_xml))
            root = tree.getroot()

            for record in root.iter("Record"):
                rtype = record.get("type", "")
                if rtype not in RECORD_TYPES:
                    continue

                start_str = record.get("startDate", "")
                end_str = record.get("endDate", "")
                start_ts = _parse_date(start_str)
                end_ts = _parse_date(end_str)

                if start_ts <= self._last_ts:
                    continue

                metric = RECORD_TYPES[rtype]
                value_str = record.get("value", "")
                unit = record.get("unit", "")

                if metric == "sleep":
                    if record.get("value") == _SLEEP_ASLEEP_VALUE:
                        date_key = start_str[:10]
                        hours = (end_ts - start_ts) / 3600
                        daily_sleep[date_key] = daily_sleep.get(date_key, 0.0) + hours
                    continue

                try:
                    value = float(value_str)
                except (ValueError, TypeError):
                    continue

                events.append({
                    "type": "health_metric",
                    "source": "apple_health",
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "timestamp": start_ts,
                    "_local_only": True,
                })

                if start_ts > max_ts:
                    max_ts = start_ts

            # Emit sleep summaries as events
            for date_key, hours in daily_sleep.items():
                events.append({
                    "type": "health_metric",
                    "source": "apple_health",
                    "metric": "sleep_hours",
                    "value": hours,
                    "unit": "hr",
                    "date": date_key,
                    "_local_only": True,
                })

            # Check anomalies in this batch
            metric_values: dict[str, list[float]] = {}
            for e in events:
                m = e.get("metric", "")
                v = e.get("value", 0.0)
                metric_values.setdefault(m, []).append(v)

            # Add sleep hours to metric_values
            for date_key, hours in daily_sleep.items():
                metric_values.setdefault("sleep_hours", []).append(hours)

            anomalies: list[dict] = []
            for metric_name, check_fn, msg_tmpl in ANOMALY_RULES:
                values = metric_values.get(metric_name, [])
                if values:
                    latest = values[-1]
                    if check_fn(latest):
                        anomalies.append({
                            "type": "health_anomaly",
                            "source": "apple_health",
                            "metric": metric_name,
                            "value": latest,
                            "message": msg_tmpl.format(v=latest),
                            "_local_only": True,
                        })

            events.extend(anomalies)

            if max_ts > self._last_ts:
                self._save_cursor(max_ts)

            self._ok({"records_processed": len(events), "anomalies": len(anomalies)})
        except ET.ParseError as e:
            self._fail(f"XML parse error: {e}")
            logger.error("Health XML parse error: %s", e)
        except Exception as e:
            self._fail(str(e))
            logger.error("Health poll error: %s", e)

        return events

    def health_check(self) -> IntegrationHealth:
        if not self._export_xml.exists():
            self._health.status = "down"
            self._health.error = f"No export found at {self._export_xml}"
        return self._health
