"""Entity extractor — pull structured entities from raw text.

Strategy: regex + heuristics first (fast, no LLM cost), LLM fallback
only for ambiguous cases. This module never calls external APIs directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas.world.models import EntityType


@dataclass
class ExtractedMention:
    type: str
    name: str
    confidence: float
    context: str  # surrounding text snippet


# Email address pattern
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

# Person name heuristics: Title Case words that aren't common stopwords
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "during",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "hello", "hi", "dear", "thanks", "thank", "please", "hey", "ok", "okay",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "atlas", "ai", "llm", "api", "gpt", "gemini", "groq",
}

# Known project indicators
_PROJECT_INDICATORS = re.compile(
    r'\b(project|repo|repository|app|service|system|tool|library|framework|platform|product|feature|module|dashboard|pipeline|backend|frontend|api|sdk)\b',
    re.IGNORECASE,
)

# GitHub repo pattern: owner/repo
_GITHUB_REPO_RE = re.compile(r'\b([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)\b')

# URL pattern
_URL_RE = re.compile(r'https?://[^\s<>"]+')

# Commitment patterns: "I will X", "I'll X", "promise to X", "committed to X"
_COMMITMENT_RE = re.compile(
    r"(i(?:'ll| will| promise to| committed to| need to| have to| should)\s+([^.!?\n]{5,60}))",
    re.IGNORECASE,
)

# Meeting/place patterns
_PLACE_RE = re.compile(
    r'\b(at|in|@)\s+([A-Z][a-zA-Z\s]{2,30}(?:office|room|cafe|coffee|building|hall|center|centre|hq|headquarters))\b',
    re.IGNORECASE,
)


def extract_from_text(text: str, source: str = "llm_inference") -> list[ExtractedMention]:
    """Extract entity mentions from raw text using heuristics."""
    mentions: list[ExtractedMention] = []
    seen_names: set[str] = set()

    def add(type: str, name: str, conf: float, ctx: str = "") -> None:
        name_lower = name.lower().strip()
        if name_lower not in seen_names and len(name.strip()) > 1:
            seen_names.add(name_lower)
            mentions.append(ExtractedMention(type=type, name=name.strip(), confidence=conf, context=ctx[:100]))

    # Extract email addresses → Person entities
    for m in _EMAIL_RE.finditer(text):
        email = m.group(0)
        # Email username often is the person's name
        username = email.split("@")[0].replace(".", " ").replace("_", " ").title()
        ctx = text[max(0, m.start()-30):m.end()+30]
        add(EntityType.PERSON, username, 0.7, ctx)
        # Also store email as attribute hint — encoded in context
        add(EntityType.PERSON, email, 0.5, ctx)  # store raw email too for attribute extraction

    # Extract GitHub repos → Project entities
    for m in _GITHUB_REPO_RE.finditer(text):
        repo = m.group(1)
        if "/" in repo and not repo.startswith("http"):
            ctx = text[max(0, m.start()-20):m.end()+20]
            add(EntityType.PROJECT, repo, 0.8, ctx)

    # Extract Title Case multi-word names as Person candidates
    # Look for 2-3 consecutive Title Case words not in stopwords
    words = text.split()
    i = 0
    while i < len(words):
        word = re.sub(r'[^\w\s]', '', words[i])
        if (word and word[0].isupper() and word.lower() not in _STOPWORDS
                and len(word) > 2 and word.isalpha()):
            # Check if next word is also Title Case → likely a name
            name_parts = [word]
            j = i + 1
            while j < len(words) and j < i + 3:
                next_word = re.sub(r'[^\w\s]', '', words[j])
                if (next_word and next_word[0].isupper()
                        and next_word.lower() not in _STOPWORDS
                        and next_word.isalpha()):
                    name_parts.append(next_word)
                    j += 1
                else:
                    break
            if len(name_parts) >= 2:
                name = " ".join(name_parts)
                start_idx = text.find(word)
                ctx = text[max(0, start_idx-20):start_idx+len(name)+20]
                add(EntityType.PERSON, name, 0.5, ctx)
        i += 1

    # Extract project names from context indicators
    for m in _PROJECT_INDICATORS.finditer(text):
        # Look for quoted or code-formatted project name nearby
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        snippet = text[start:end]
        # Look for backtick-quoted names
        backtick = re.search(r'`([^`]{2,40})`', snippet)
        if backtick:
            add(EntityType.PROJECT, backtick.group(1), 0.8, snippet)
        # Look for quoted names
        quoted = re.search(r'"([^"]{2,40})"', snippet)
        if quoted:
            add(EntityType.PROJECT, quoted.group(1), 0.7, snippet)

    # Extract commitments
    for m in _COMMITMENT_RE.finditer(text):
        commitment_text = m.group(1)
        add(EntityType.COMMITMENT, commitment_text[:80], 0.6, commitment_text)

    # Extract places
    for m in _PLACE_RE.finditer(text):
        add(EntityType.PLACE, m.group(2).strip(), 0.6, m.group(0))

    return mentions


def extract_from_email(
    sender: str,
    subject: str,
    body: str,
) -> list[ExtractedMention]:
    """Email-specific extraction — sender is high-confidence Person."""
    mentions: list[ExtractedMention] = []
    seen: set[str] = set()

    def add(type: str, name: str, conf: float, ctx: str = "") -> None:
        key = name.lower().strip()
        if key not in seen and len(name.strip()) > 1:
            seen.add(key)
            mentions.append(ExtractedMention(type=type, name=name.strip(), confidence=conf, context=ctx[:100]))

    # Sender is a high-confidence person
    if sender:
        # "Name Surname <email@domain>" format
        m = re.match(r'^([^<]+)\s*<', sender)
        if m:
            add(EntityType.PERSON, m.group(1).strip(), 0.9, f"email sender: {sender}")
        else:
            add(EntityType.PERSON, sender.split("@")[0].replace(".", " ").title(), 0.7, sender)

    # Extract from subject and body
    for mention in extract_from_text(subject + " " + body, source="gmail"):
        key = mention.name.lower().strip()
        if key not in seen:
            seen.add(key)
            mentions.append(mention)

    return mentions


def extract_from_git_commit(message: str, author: str) -> list[ExtractedMention]:
    """Git commit — author is Person, message may contain project/topic refs."""
    mentions: list[ExtractedMention] = []
    seen: set[str] = set()

    def add(type: str, name: str, conf: float) -> None:
        key = name.lower().strip()
        if key not in seen:
            seen.add(key)
            mentions.append(ExtractedMention(type=type, name=name.strip(), confidence=conf, context=message[:80]))

    if author:
        add(EntityType.PERSON, author, 0.95)

    # First word of commit message often indicates project area
    parts = message.strip().split(":")
    if len(parts) > 1:
        scope = parts[0].strip().lower()
        if scope and len(scope) < 30 and scope not in {"fix", "feat", "chore", "docs", "test", "refactor", "style", "ci", "build"}:
            add(EntityType.PROJECT, scope, 0.7)

    # Look for explicit project refs
    for mention in extract_from_text(message, source="git"):
        if mention.type == EntityType.PROJECT:
            key = mention.name.lower().strip()
            if key not in seen:
                seen.add(key)
                mentions.append(mention)

    return mentions
