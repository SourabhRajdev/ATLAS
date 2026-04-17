"""Real-world signal sources. Each exposes async poll() -> list[Signal]."""

from atlas.autonomy.sources.calendar import CalendarSource
from atlas.autonomy.sources.mail import MailSource
from atlas.autonomy.sources.files import FilesSource
from atlas.autonomy.sources.git import GitSource
from atlas.autonomy.sources.battery import BatterySource
from atlas.autonomy.sources.clipboard import ClipboardSource

__all__ = [
    "CalendarSource",
    "MailSource",
    "FilesSource",
    "GitSource",
    "BatterySource",
    "ClipboardSource",
]
