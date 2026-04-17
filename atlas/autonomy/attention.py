"""Attention system — decides what needs user attention."""

from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import types

from atlas.autonomy.models import ActionDecision, Attention, Priority, Signal
from atlas.config import Settings

logger = logging.getLogger("atlas.attention")

ATTENTION_PROMPT = """\
You are an attention system. Analyze signals and decide what needs user attention.

For each signal, decide:
1. action: notify (tell user) | act (take action) | ignore (do nothing)
2. priority: low | medium | high
3. confidence: 0.0 to 1.0 (how sure you are)
4. reason: why you made this decision

Rules:
- HIGH priority: urgent, time-sensitive, or important
- MEDIUM priority: helpful but not urgent
- LOW priority: nice-to-know, can wait
- High confidence (>0.8): clear, obvious decision
- Low confidence (<0.5): uncertain, need user input
- NOTIFY: user should know about this
- ACT: system can handle this autonomously
- IGNORE: not important right now

Signal: {signal}

Output JSON:
{{
  "action": "notify|act|ignore",
  "priority": "low|medium|high",
  "confidence": 0.0-1.0,
  "reason": "explanation",
  "suggested_response": "what to say/do (optional)"
}}
"""


class AttentionSystem:
    """Decides what signals need attention and how to handle them."""
    
    def __init__(self, config: Settings, client: genai.Client) -> None:
        self.config = config
        self.client = client

    async def evaluate_signal(self, signal: Signal) -> Attention:
        """Evaluate a signal and decide what to do."""
        
        # Build prompt
        signal_desc = f"""
Type: {signal.type}
Source: {signal.source}
Description: {signal.description}
Data: {signal.data}
"""
        
        prompt = ATTENTION_PROMPT.format(signal=signal_desc)
        
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                )
            )
            
            if not response.candidates:
                return self._fallback_attention(signal)
            
            # Parse response
            import json
            result = json.loads(response.candidates[0].content.parts[0].text)
            
            attention = Attention(
                signal_id=signal.id,
                action=ActionDecision(result["action"]),
                priority=Priority(result["priority"]),
                confidence=float(result["confidence"]),
                reason=result["reason"],
                suggested_response=result.get("suggested_response"),
            )
            
            logger.info(
                "Attention decision for %s: %s (priority=%s, confidence=%.2f)",
                signal.type, attention.action, attention.priority, attention.confidence
            )
            
            return attention
            
        except Exception as e:
            logger.error("Attention evaluation failed: %s", e)
            return self._fallback_attention(signal)

    def _fallback_attention(self, signal: Signal) -> Attention:
        """Fallback attention decision when LLM fails."""
        
        # Conservative defaults
        if signal.type == "scheduled_task":
            action = ActionDecision.ACT
            priority = Priority.MEDIUM
            confidence = 0.9
            reason = "Scheduled task is due"
        elif signal.type == "anomaly":
            action = ActionDecision.NOTIFY
            priority = Priority.HIGH
            confidence = 0.8
            reason = "Anomaly detected, user should know"
        elif signal.type == "suggestion":
            action = ActionDecision.NOTIFY
            priority = Priority.LOW
            confidence = 0.6
            reason = "Suggestion for user consideration"
        else:
            action = ActionDecision.IGNORE
            priority = Priority.LOW
            confidence = 0.5
            reason = "Unknown signal type, ignoring"
        
        return Attention(
            signal_id=signal.id,
            action=action,
            priority=priority,
            confidence=confidence,
            reason=reason,
        )

    async def batch_evaluate(self, signals: list[Signal]) -> list[Attention]:
        """Evaluate multiple signals."""
        attentions = []
        
        for signal in signals:
            attention = await self.evaluate_signal(signal)
            attentions.append(attention)
        
        return attentions
