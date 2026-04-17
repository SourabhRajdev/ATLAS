"""Continuous autonomy loop — the heart of proactive intelligence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from google import genai

from atlas.autonomy.attention import AttentionSystem
from atlas.autonomy.gating import ContextGate
from atlas.autonomy.logger import AutonomyLogger
from atlas.autonomy.models import ActionDecision, Attention, Priority, ProactiveAction, Signal
from atlas.autonomy.scoring import SignalScorer
from atlas.autonomy.signals import SignalDetector
from atlas.config import Settings
from atlas.interfaces.presence import create_presence_layer
from atlas.memory.store import MemoryStore
from atlas.scheduler.scheduler import Scheduler
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger("atlas.autonomy")


class AutonomyLoop:
    """Continuous background loop for proactive intelligence."""
    
    def __init__(
        self,
        config: Settings,
        memory: MemoryStore,
        scheduler: Scheduler,
        tools: ToolRegistry,
        client: genai.Client,
    ) -> None:
        self.config = config
        self.memory = memory
        self.scheduler = scheduler
        self.tools = tools
        self.client = client
        
        self.signal_detector = SignalDetector(memory, scheduler)
        self.attention_system = AttentionSystem(config, client)
        self.signal_scorer = SignalScorer()
        self.context_gate = ContextGate()
        self.autonomy_logger = AutonomyLogger(config.data_dir / "autonomy.log")
        self.presence = create_presence_layer(voice_mode=False)  # For notifications
        
        self.running = False
        self.interval = config.autonomy_interval
        self.cycle_count = 0
        
        # Kill switch: suppress if too many low-value outputs
        self.recent_outputs: list[datetime] = []
        self.kill_switch_active = False
        self.kill_switch_threshold = 3  # outputs
        self.kill_switch_window = 300  # 5 minutes
        
        # Callbacks
        self.notify_callback: Callable[[str, Priority], None] | None = None
        self.approval_callback: Callable[[str], bool] | None = None

    def set_notify_callback(self, fn: Callable[[str, Priority], None]) -> None:
        """Set callback for notifications."""
        self.notify_callback = fn

    def set_approval_callback(self, fn: Callable[[str], bool]) -> None:
        """Set callback for approvals."""
        self.approval_callback = fn

    async def start(self) -> None:
        """Start the autonomy loop."""
        self.running = True
        logger.info("Autonomy loop started (interval: %ds)", self.interval)
        
        while self.running:
            try:
                await self._cycle()
            except Exception as e:
                logger.error("Autonomy loop error: %s", e)
            
            # Wait before next cycle
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """Stop the autonomy loop."""
        self.running = False
        logger.info("Autonomy loop stopped")

    def _check_kill_switch(self) -> None:
        """Check if kill switch should activate."""
        now = datetime.now(timezone.utc)
        self.recent_outputs.append(now)
        
        # Remove old outputs outside window
        cutoff = now - timedelta(seconds=self.kill_switch_window)
        self.recent_outputs = [t for t in self.recent_outputs if t > cutoff]
        
        # Check if too many outputs
        if len(self.recent_outputs) >= self.kill_switch_threshold:
            self.kill_switch_active = True
            logger.warning(
                "Kill switch activated: %d outputs in %ds",
                len(self.recent_outputs),
                self.kill_switch_window
            )
            # Auto-reset after 30 minutes
            asyncio.create_task(self._reset_kill_switch())

    async def _reset_kill_switch(self) -> None:
        """Reset kill switch after cooldown."""
        await asyncio.sleep(1800)  # 30 minutes
        self.kill_switch_active = False
        self.recent_outputs.clear()
        logger.info("Kill switch reset")

    def get_state(self) -> dict:
        """Get current autonomy state."""
        stats = self.autonomy_logger.get_statistics()
        signal_types = self.autonomy_logger.get_signal_types()
        
        return {
            "running": self.running,
            "cycle_count": self.cycle_count,
            "interval": self.interval,
            "kill_switch_active": self.kill_switch_active,
            "statistics": stats,
            "signal_types": signal_types,
            "context": self.context_gate.get_context_info(),
        }

    def get_recent_decisions(self, limit: int = 10) -> list[dict]:
        """Get recent autonomy decisions."""
        return self.autonomy_logger.get_recent_decisions(limit)

    async def _cycle(self) -> None:
        """Single cycle of the autonomy loop."""
        
        self.cycle_count += 1
        
        # Check kill switch
        if self.kill_switch_active:
            logger.info("Kill switch active, skipping cycle")
            return
        
        # 1. Detect signals
        try:
            signals = await self.signal_detector.detect_all()
        except Exception as e:
            logger.error("Signal detection failed: %s", e)
            self.autonomy_logger.log_cycle(
                self.cycle_count,
                detected_signals=[],
                filtered_signals=[],
                selected_signal=None,
                decision={"action": "ignore", "reason": f"Signal detection error: {e}"},
            )
            return
        
        detected_signals = [
            {"type": s.type, "description": s.description, "data": s.data}
            for s in signals
        ]
        
        if not signals:
            self.autonomy_logger.log_cycle(
                self.cycle_count,
                detected_signals=[],
                filtered_signals=[],
                selected_signal=None,
                decision=None,
            )
            return
        
        logger.debug("Detected %d raw signals", len(signals))
        
        # 2. Score and filter signals
        context = self.context_gate.get_context_info()
        scored_signals = []
        
        for signal in signals:
            try:
                score = self.signal_scorer.score_signal(signal, context)
                
                # Skip if should ignore
                if score["should_ignore"]:
                    logger.debug("Ignoring signal: %s (score: %.2f)", signal.description, score["final_priority"])
                    continue
                
                # Check context gating
                if self.context_gate.should_suppress(signal.type, score["priority_level"].value):
                    logger.debug("Suppressed by context gate: %s", signal.description)
                    continue
                
                scored_signals.append((signal, score))
            except Exception as e:
                logger.error("Signal scoring failed for %s: %s", signal.type, e)
                continue
        
        filtered_signals = [
            {
                "type": s.type,
                "description": s.description,
                "priority": score["final_priority"],
                "reason": score["reason"]
            }
            for s, score in scored_signals
        ]
        
        if not scored_signals:
            logger.debug("No signals passed filtering")
            self.autonomy_logger.log_cycle(
                self.cycle_count,
                detected_signals=detected_signals,
                filtered_signals=[],
                selected_signal=None,
                decision=None,
            )
            return
        
        # 3. Pick ONLY the highest priority signal (max 1 per cycle)
        scored_signals.sort(key=lambda x: x[1]["final_priority"], reverse=True)
        top_signal, top_score = scored_signals[0]
        
        selected_signal = {
            "type": top_signal.type,
            "description": top_signal.description,
            "priority": top_score["final_priority"],
            "reason": top_score["reason"],
        }
        
        logger.info(
            "Selected signal: %s (priority: %.2f, %s)",
            top_signal.description,
            top_score["final_priority"],
            top_score["priority_level"].value
        )
        
        # 4. Evaluate attention for top signal only
        try:
            attention = await self.attention_system.evaluate_signal(top_signal)
        except Exception as e:
            logger.error("Attention evaluation failed: %s", e)
            self.autonomy_logger.log_cycle(
                self.cycle_count,
                detected_signals=detected_signals,
                filtered_signals=filtered_signals,
                selected_signal=selected_signal,
                decision={"action": "ignore", "reason": f"LLM error: {e}"},
            )
            return
        
        decision = {
            "action": attention.action.value,
            "priority": attention.priority.value,
            "confidence": attention.confidence,
            "reason": attention.reason,
        }
        
        # 5. Process the single selected signal
        try:
            execution_result = await self._process_attention(top_signal, attention, top_score)
        except Exception as e:
            logger.error("Execution failed: %s", e)
            execution_result = f"error: {e}"
        
        # 6. Log complete cycle
        self.autonomy_logger.log_cycle(
            self.cycle_count,
            detected_signals=detected_signals,
            filtered_signals=filtered_signals,
            selected_signal=selected_signal,
            decision=decision,
            execution_result=execution_result,
        )
        
        # 7. Mark as shown (start cooldown)
        if attention.action != ActionDecision.IGNORE:
            self.signal_scorer.mark_shown(top_signal)
            self._check_kill_switch()

    async def _process_attention(self, signal: Signal, attention: Attention, score: dict) -> str:
        """Process an attention decision. Returns execution result."""
        
        if attention.action == ActionDecision.IGNORE:
            logger.debug("Ignoring signal: %s", signal.description)
            return "ignored"
        
        if attention.action == ActionDecision.NOTIFY:
            await self._notify_user(signal, attention, score)
            return "notified"
        
        elif attention.action == ActionDecision.ACT:
            result = await self._take_action(signal, attention, score)
            return result or "acted"
        
        return "unknown"

    async def _notify_user(self, signal: Signal, attention: Attention, score: dict) -> None:
        """Notify user about a signal (clean, minimal output)."""
        
        # Build clean notification
        lines = []
        lines.append(f"💡 {signal.description}")
        
        if attention.suggested_response:
            lines.append(f"   {attention.suggested_response}")
        
        # Add "why" explanation (compressed)
        lines.append(f"   Why: {attention.reason}")
        lines.append(f"   Confidence: {attention.confidence:.0%} | {score['reason']}")
        
        message = "\n".join(lines)
        
        # Compress for voice if callback expects it
        compressed_message = self.presence.format_proactive_notification(message)
        
        if self.notify_callback:
            # Send compressed version for voice, full for CLI
            self.notify_callback(compressed_message, attention.priority)
        else:
            logger.info("NOTIFY:\n%s", message)

    async def _take_action(self, signal: Signal, attention: Attention, score: dict) -> str:
        """Take autonomous action on a signal. Returns result."""
        
        # HARD RULE: Only act if confidence is high enough
        if attention.confidence < 0.7:
            logger.info("Confidence too low for autonomous action: %.0%", attention.confidence * 100)
            # Downgrade to notification instead
            await self._notify_user(signal, attention, score)
            return "downgraded_to_notify"
        
        # HARD RULE: Only act on reversible actions
        if signal.type not in ("scheduled_task",):
            logger.info("Signal type not approved for autonomous action: %s", signal.type)
            await self._notify_user(signal, attention, score)
            return "downgraded_to_notify"
        
        # Execute based on signal type
        if signal.type == "scheduled_task":
            result = await self._execute_scheduled_task(signal, attention)
            return result
        else:
            # Log the proactive action
            action = ProactiveAction(
                signal_id=signal.id,
                action_type="notify",
                description=signal.description,
                confidence=attention.confidence,
                why=attention.reason,
                data_used=[signal.source],
                result="Notified user",
            )
            logger.info("Proactive action: %s", action.description)
            return "logged"

    async def _execute_scheduled_task(self, signal: Signal, attention: Attention) -> str:
        """Execute a scheduled task. Returns result."""
        
        task_id = signal.data.get("task_id")
        task_type = signal.data.get("task_type")
        target = signal.data.get("target")
        params = signal.data.get("params", {})
        
        logger.info("Executing scheduled task: %s", target)
        
        try:
            if task_type == "tool":
                # Execute tool
                record = await self.tools.execute(target, params)
                success = record.error is None
                result = str(record.result if success else record.error)
            elif task_type == "workflow":
                # Execute workflow (would need workflow engine)
                result = f"Workflow '{target}' execution not yet implemented"
                success = False
            else:
                result = f"Unknown task type: {task_type}"
                success = False
            
            # Update scheduler
            self.scheduler.update_task_after_run(task_id, success)
            
            # Log proactive action
            action = ProactiveAction(
                signal_id=signal.id,
                action_type="execute_tool" if task_type == "tool" else "execute_workflow",
                description=f"Executed scheduled task: {target}",
                confidence=attention.confidence,
                why=attention.reason,
                data_used=["scheduler"],
                result=result,
            )
            
            # Notify user of completion
            if self.notify_callback:
                self.notify_callback(
                    f"Completed scheduled task: {target}\n  Result: {result}",
                    Priority.LOW
                )
            
            return f"executed: {result[:100]}"
            
        except Exception as e:
            logger.error("Failed to execute scheduled task: %s", e)
            self.scheduler.update_task_after_run(task_id, False)
            return f"failed: {e}"
