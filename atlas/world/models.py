"""Data models for the World Model — Entity, Attribute, Relationship, WorldEvent.

Does NOT contain database logic. Does NOT contain extraction logic.
Pure dataclasses that flow between WorldModel, Updater, and Assembler.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    PERSON = "Person"
    PROJECT = "Project"
    COMMITMENT = "Commitment"
    PATTERN = "Pattern"
    PLACE = "Place"
    TOPIC = "Topic"


# Source reliability order (higher index = more reliable)
SOURCE_RELIABILITY: dict[str, float] = {
    "llm_inference": 0.4,
    "web_search": 0.5,
    "file_system": 0.6,
    "git": 0.7,
    "calendar": 0.75,
    "imessage": 0.8,
    "gmail": 0.9,
    "user": 1.0,
}


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


@dataclass
class Entity:
    id: str
    type: str
    name: str
    canonical_name: str
    confidence: float
    first_seen: float
    last_updated: float
    last_reinforced: float
    source: str
    metadata: dict = field(default_factory=dict)
    embedding: bytes | None = None

    @classmethod
    def new(
        cls,
        type: str,
        name: str,
        source: str,
        metadata: dict | None = None,
    ) -> "Entity":
        now = _now()
        return cls(
            id=_uid(),
            type=type,
            name=name,
            canonical_name=_canonicalize(name),
            confidence=SOURCE_RELIABILITY.get(source, 0.6),
            first_seen=now,
            last_updated=now,
            last_reinforced=now,
            source=source,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "canonical_name": self.canonical_name,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "last_reinforced": self.last_reinforced,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class Attribute:
    id: int
    entity_id: str
    key: str
    value: str
    confidence: float
    source: str
    recorded_at: float
    superseded_by: int | None = None

    def is_current(self) -> bool:
        return self.superseded_by is None


@dataclass
class Relationship:
    id: int
    from_entity: str
    to_entity: str
    relation_type: str
    strength: float
    first_seen: float
    last_seen: float
    source: str


@dataclass
class WorldEvent:
    event_type: str
    source: str
    payload: dict
    id: int = 0
    processed: bool = False
    processed_at: float | None = None
    entities_affected: list[str] = field(default_factory=list)
    recorded_at: float = field(default_factory=_now)

    def payload_json(self) -> str:
        return json.dumps(self.payload, default=str)


@dataclass
class WorldContext:
    """Assembled context ready to inject into an LLM prompt."""
    text: str
    token_estimate: int
    entities_included: list[str]
    truncated: bool = False

    def is_empty(self) -> bool:
        return not self.text.strip()


def _canonicalize(name: str) -> str:
    """Normalize name for deduplication — lowercase, strip, collapse spaces."""
    return " ".join(name.lower().strip().split())
