"""Presence layer — Makes ATLAS feel alive, not like a tool."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Acknowledgement cues for latency >500ms
ACKNOWLEDGEMENTS = [
    "yeah",
    "got it",
    "one sec",
    "sure",
    "okay",
]

# Response compression patterns
COMPRESSION_RULES = [
    # Time queries
    (r"The current time is (\d+:\d+\s*(?:AM|PM)?)", r"\1"),
    (r"It is currently (\d+:\d+\s*(?:AM|PM)?)", r"\1"),
    
    # Date queries
    (r"Today is (.+)", r"\1"),
    (r"The date is (.+)", r"\1"),
    
    # File operations
    (r"I have created (?:a file|the file) (?:called |named )?(.+)", r"created \1"),
    (r"I have deleted (?:the file |)(.+)", r"deleted \1"),
    (r"I have written to (.+)", r"wrote to \1"),
    
    # Search results
    (r"I found (\d+) results?", r"\1 results"),
    (r"Here are the search results:", r"found:"),
    
    # Confirmations
    (r"I have completed the task", r"done"),
    (r"The task has been completed", r"done"),
    (r"Successfully (.+)", r"\1"),
    
    # Status
    (r"The system is (.+)", r"\1"),
    (r"Everything is (.+)", r"\1"),
    
    # Generic verbose patterns
    (r"I will (.+) for you", r"\1"),
    (r"Let me (.+) for you", r"\1"),
    (r"I can help you (?:with |by )?(.+)", r"\1"),
]


class PresenceLayer:
    """Transforms responses to feel natural and alive."""
    
    def __init__(self, voice_mode: bool = False) -> None:
        self.voice_mode = voice_mode
        self.last_acknowledgement_index = 0
    
    def compress_response(self, text: str, user_query: str = "") -> str:
        """Compress verbose responses to be short and direct."""
        if not text or not text.strip():
            return text
        
        # Apply compression rules
        compressed = text
        for pattern, replacement in COMPRESSION_RULES:
            compressed = re.sub(pattern, replacement, compressed, flags=re.IGNORECASE)
        
        # Remove unnecessary politeness in voice mode
        if self.voice_mode:
            compressed = self._remove_politeness(compressed)
        
        # Remove redundant explanations
        compressed = self._remove_explanations(compressed)
        
        return compressed.strip()
    
    def _remove_politeness(self, text: str) -> str:
        """Remove unnecessary politeness markers."""
        # Remove common polite phrases
        polite_phrases = [
            r"^(?:Sure|Certainly|Of course|Absolutely)[,.]?\s*",
            r"^(?:I'd be happy to|I'll be glad to)\s+",
            r"^(?:No problem|You're welcome)[,.]?\s*",
        ]
        
        result = text
        for phrase in polite_phrases:
            result = re.sub(phrase, "", result, flags=re.IGNORECASE)
        
        return result.strip()
    
    def _remove_explanations(self, text: str) -> str:
        """Remove unnecessary explanations unless they're the main content."""
        # If response is already short, keep it
        if len(text) < 100:
            return text
        
        # Split into sentences
        sentences = re.split(r'[.!?]\s+', text)
        
        # Keep first sentence (usually the answer)
        # Remove explanatory sentences
        explanatory_markers = [
            "this is because",
            "the reason is",
            "this means",
            "in other words",
            "to clarify",
            "for example",
        ]
        
        filtered = []
        for sentence in sentences:
            sentence_lower = sentence.lower()
            is_explanatory = any(marker in sentence_lower for marker in explanatory_markers)
            
            if not is_explanatory or len(filtered) == 0:
                filtered.append(sentence)
        
        return ". ".join(filtered).strip()
    
    def get_acknowledgement(self) -> str:
        """Get a short acknowledgement cue for latency >500ms."""
        ack = ACKNOWLEDGEMENTS[self.last_acknowledgement_index % len(ACKNOWLEDGEMENTS)]
        self.last_acknowledgement_index += 1
        return ack
    
    def format_for_voice(self, text: str) -> str:
        """Format text specifically for voice output."""
        if not text:
            return text
        
        # Break long sentences
        formatted = self._break_long_sentences(text)
        
        # Remove markdown formatting
        formatted = self._remove_markdown(formatted)
        
        # Simplify numbers
        formatted = self._simplify_numbers(formatted)
        
        return formatted.strip()
    
    def _break_long_sentences(self, text: str) -> str:
        """Break long sentences into shorter ones for better speech flow."""
        # Split on conjunctions if sentence is too long
        sentences = re.split(r'[.!?]\s+', text)
        
        result = []
        for sentence in sentences:
            if len(sentence) > 150:
                # Try to split on conjunctions
                parts = re.split(r',\s+(?:and|but|or|so)\s+', sentence)
                result.extend(parts)
            else:
                result.append(sentence)
        
        return ". ".join(result)
    
    def _remove_markdown(self, text: str) -> str:
        """Remove markdown formatting for voice."""
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '[code block]', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Remove bold/italic
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        
        # Remove links
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # Remove headers
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
        
        return text
    
    def _simplify_numbers(self, text: str) -> str:
        """Simplify number formatting for voice."""
        # Convert large numbers to readable format
        text = re.sub(r'(\d+)000000', r'\1 million', text)
        text = re.sub(r'(\d+)000', r'\1 thousand', text)
        
        return text
    
    def should_acknowledge(self, latency_ms: int) -> bool:
        """Determine if we should send an acknowledgement cue."""
        return latency_ms > 500
    
    def format_proactive_notification(self, text: str) -> str:
        """Format proactive notifications to be non-intrusive."""
        # Keep notifications very short
        if len(text) > 100:
            # Take first sentence only
            sentences = re.split(r'[.!?]\s+', text)
            text = sentences[0] if sentences else text
        
        # Remove emoji and formatting
        text = re.sub(r'[💡🤖⚠️✅❌]', '', text)
        
        # Compress
        text = self.compress_response(text)
        
        return text.strip()


def create_presence_layer(voice_mode: bool = False) -> PresenceLayer:
    """Factory function to create presence layer."""
    return PresenceLayer(voice_mode=voice_mode)
