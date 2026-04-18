"""Pattern analyzer and weekly report generator.

Runs over quality signals to identify recurring problems and wins.
Produces WeeklyReport with actionable recommendations.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from atlas.improvement.models import (
    BehaviorPattern, ImpactLevel, QualitySignal, SignalKind, WeeklyReport
)
from atlas.improvement.monitor import BehaviorMonitor

logger = logging.getLogger("atlas.improvement.analyzer")

ROLLING_WINDOW_DAYS = 7
PATTERN_THRESHOLD = 3   # min occurrences to call it a pattern

_RECOMMENDATIONS: dict[SignalKind, str] = {
    SignalKind.TASK_DURATION_OVERRUN:
        "Break large tasks into smaller sub-tasks with tighter estimates.",
    SignalKind.TASK_REPEATED_FAILURE:
        "Tasks failing repeatedly may need a different approach — consider asking for help.",
    SignalKind.TOOL_ERROR_SPIKE:
        "Tool errors are spiking — check API limits, credentials, or network connectivity.",
    SignalKind.USER_CORRECTION:
        "Frequent user corrections suggest output quality needs attention. Re-read instructions more carefully.",
    SignalKind.NEGATIVE_FEEDBACK:
        "User expressed dissatisfaction multiple times. Ask clarifying questions before acting.",
    SignalKind.RESPONSE_TOO_LONG:
        "Responses are too long. Default to concise outputs unless explicitly asked for detail.",
    SignalKind.CONTEXT_LOST:
        "Context is being lost between exchanges. Summarize key facts at the start of complex tasks.",
    SignalKind.GOAL_ABANDONED:
        "Goals are being abandoned — they may be too ambitious. Break them into smaller milestones.",
}

_POSITIVE_RECOMMENDATIONS: dict[SignalKind, str] = {
    SignalKind.POSITIVE_FEEDBACK: "Keep up the current approach — users are responding well.",
    SignalKind.USER_APPROVAL: "Actions are being approved — judgment calls are well-calibrated.",
    SignalKind.GOAL_COMPLETED: "Goals are getting done — maintain this momentum.",
}


def _current_week_key() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


class BehaviorAnalyzer:
    def __init__(self, monitor: BehaviorMonitor) -> None:
        self._monitor = monitor

    def identify_patterns(self, signals: list[QualitySignal]) -> list[BehaviorPattern]:
        by_kind: defaultdict[str, list[QualitySignal]] = defaultdict(list)
        for sig in signals:
            by_kind[sig.kind.value].append(sig)

        patterns: list[BehaviorPattern] = []
        for kind_str, kind_signals in by_kind.items():
            if len(kind_signals) < PATTERN_THRESHOLD:
                continue
            contexts = list({s.context for s in kind_signals})[:5]
            impact_counts = Counter(s.impact for s in kind_signals)
            dominant_impact = impact_counts.most_common(1)[0][0]
            ts_list = [s.recorded_at for s in kind_signals]

            try:
                kind = SignalKind(kind_str)
                rec = _RECOMMENDATIONS.get(kind) or _POSITIVE_RECOMMENDATIONS.get(kind, "")
            except ValueError:
                rec = ""

            patterns.append(BehaviorPattern(
                pattern_type=kind_str,
                frequency=len(kind_signals),
                impact=dominant_impact,
                contexts=contexts,
                first_seen=min(ts_list),
                last_seen=max(ts_list),
                recommendation=rec,
            ))

        patterns.sort(key=lambda p: (
            0 if p.impact == ImpactLevel.NEGATIVE else 1,
            -p.frequency
        ))
        return patterns

    def generate_weekly_report(self) -> WeeklyReport:
        week_key = _current_week_key()
        signals = self._monitor.get_recent_signals(days=ROLLING_WINDOW_DAYS)
        patterns = self.identify_patterns(signals)

        positive = [s for s in signals if s.impact == ImpactLevel.POSITIVE]
        negative = [s for s in signals if s.impact == ImpactLevel.NEGATIVE]

        recommendations: list[str] = []
        for p in patterns:
            if p.recommendation and p.is_concerning:
                recommendations.append(p.recommendation)

        # Positive reinforcement
        for p in patterns:
            if p.impact == ImpactLevel.POSITIVE and p.recommendation:
                recommendations.append(p.recommendation)

        if not recommendations:
            recommendations.append("No significant patterns this week — keep going.")

        summary = self._format_summary(week_key, signals, patterns, positive, negative)

        report = WeeklyReport(
            week_key=week_key,
            generated_at=time.time(),
            total_signals=len(signals),
            positive_count=len(positive),
            negative_count=len(negative),
            patterns=patterns,
            recommendations=recommendations,
            summary=summary,
        )

        # Persist
        report_dict = {
            "week_key": report.week_key,
            "generated_at": report.generated_at,
            "total_signals": report.total_signals,
            "positive_count": report.positive_count,
            "negative_count": report.negative_count,
            "health_score": report.health_score,
            "patterns": [
                {
                    "type": p.pattern_type,
                    "frequency": p.frequency,
                    "impact": p.impact.value,
                    "contexts": p.contexts,
                    "recommendation": p.recommendation,
                }
                for p in report.patterns
            ],
            "recommendations": report.recommendations,
            "summary": report.summary,
        }
        self._monitor.save_report(week_key, json.dumps(report_dict))
        logger.info("Generated weekly report for %s: health=%.2f", week_key, report.health_score)
        return report

    def _format_summary(
        self,
        week_key: str,
        signals: list[QualitySignal],
        patterns: list[BehaviorPattern],
        positive: list[QualitySignal],
        negative: list[QualitySignal],
    ) -> str:
        if not signals:
            return f"Week {week_key}: No signals recorded. System may be idle."

        health = len(positive) / len(signals) if signals else 1.0
        health_label = "Excellent" if health > 0.8 else "Good" if health > 0.6 else "Needs attention"

        lines = [
            f"Weekly Self-Improvement Report — {week_key}",
            f"Health: {health_label} ({health:.0%} positive signals)",
            f"Signals: {len(signals)} total ({len(positive)} positive, {len(negative)} negative)",
            "",
        ]

        concerning = [p for p in patterns if p.is_concerning]
        if concerning:
            lines.append(f"Issues ({len(concerning)}):")
            for p in concerning[:3]:
                lines.append(f"  • {p.pattern_type.replace('_', ' ').title()}: "
                             f"{p.frequency}x in {', '.join(p.contexts[:2])}")

        wins = [p for p in patterns if p.impact == ImpactLevel.POSITIVE and p.frequency >= 2]
        if wins:
            lines.append(f"Wins ({len(wins)}):")
            for p in wins[:2]:
                lines.append(f"  ✓ {p.pattern_type.replace('_', ' ').title()}: {p.frequency}x")

        return "\n".join(lines)
