"""Reasoner — turns raw Signals into scored Suggestions.

Deterministic for common signals (no LLM call on the hot path). Falls back to
the LLM only when the signal has no handler — and only when budget allows.
"""

from __future__ import annotations

import logging

from atlas.autonomy.learning import SignalLearner
from atlas.autonomy.models import Confidence, Signal, Suggestion

logger = logging.getLogger("atlas.autonomy.reasoner")


class Reasoner:
    def __init__(self, learner: SignalLearner | None = None) -> None:
        self.learner = learner or SignalLearner()

    def score(self, signal: Signal) -> Suggestion | None:
        handler = getattr(self, f"_h_{signal.source}_{signal.kind}", None)
        if handler is None:
            return None
        suggestion = handler(signal)
        if suggestion is None:
            return None
        suggestion.confidence.user_wants_this = self.learner.weight(
            signal.source, signal.kind,
        )
        return suggestion

    # ---------- handlers ----------

    def _h_calendar_meeting_t5(self, s: Signal) -> Suggestion:
        title = s.payload.get("title", "meeting")
        return Suggestion(
            signal=s,
            title=f"Meeting in 5 min: {title}",
            rationale="calendar event starts soon",
            confidence=Confidence(
                signal_quality=0.95, action_correctness=0.9,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_calendar_meeting_t15(self, s: Signal) -> Suggestion:
        title = s.payload.get("title", "meeting")
        return Suggestion(
            signal=s,
            title=f"Meeting in 15 min: {title}",
            rationale="prep time",
            confidence=Confidence(
                signal_quality=0.95, action_correctness=0.8,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_calendar_meeting_now(self, s: Signal) -> Suggestion:
        title = s.payload.get("title", "meeting")
        return Suggestion(
            signal=s,
            title=f"Meeting starting: {title}",
            rationale="event is starting now",
            confidence=Confidence(
                signal_quality=0.95, action_correctness=0.95,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_mail_new_mail_from_vip(self, s: Signal) -> Suggestion:
        sender = s.payload.get("sender", "")
        subject = s.payload.get("subject", "")
        return Suggestion(
            signal=s,
            title=f"VIP mail: {sender}",
            rationale=f"subject: {subject}",
            confidence=Confidence(
                signal_quality=0.9, action_correctness=0.85,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_git_uncommitted_long(self, s: Signal) -> Suggestion:
        hours = s.payload.get("age_seconds", 0) // 3600
        return Suggestion(
            signal=s,
            title=f"Uncommitted work for {hours}h",
            rationale="working tree dirty, no recent commit",
            confidence=Confidence(
                signal_quality=0.9, action_correctness=0.7,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_battery_battery_low(self, s: Signal) -> Suggestion:
        return Suggestion(
            signal=s,
            title=f"Battery at {s.payload.get('percent')}%",
            rationale="plug in soon",
            confidence=Confidence(
                signal_quality=1.0, action_correctness=1.0,
                user_wants_this=0.5, reversibility=1.0,
            ),
        )

    def _h_battery_battery_critical(self, s: Signal) -> Suggestion:
        return Suggestion(
            signal=s,
            title=f"Battery critical: {s.payload.get('percent')}%",
            rationale="shutdown imminent",
            confidence=Confidence(
                signal_quality=1.0, action_correctness=1.0,
                user_wants_this=0.9, reversibility=1.0,
            ),
        )
