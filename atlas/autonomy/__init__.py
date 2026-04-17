"""Autonomy layer — real signals, confidence scoring, notification budget.

Sources poll the real world (calendar, mail, files, git, battery, clipboard)
and emit Signals. The Reasoner scores them, the Budget gates them, and the
Learner updates per-signal EMA weights from user feedback.
"""

from atlas.autonomy.models import Signal, Suggestion, Confidence
from atlas.autonomy.budget import NotificationBudget

__all__ = ["Signal", "Suggestion", "Confidence", "NotificationBudget"]
