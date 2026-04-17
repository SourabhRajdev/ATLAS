#!/usr/bin/env python3
"""Quick system test for ATLAS."""

import asyncio
from pathlib import Path

from atlas.config import Settings
from atlas.core.engine import Engine
from atlas.memory.store import MemoryStore
from atlas.tools.registry import ToolRegistry
from atlas.tools import filesystem, system, web, memory_tools

async def test_planning():
    """Test the planning layer."""
    print("Testing planning layer...")
    
    config = Settings()
    memory = MemoryStore(Path("~/.atlas/test.db").expanduser())
    
    registry = ToolRegistry()
    filesystem.register(registry)
    system.register(registry)
    web.register(registry)
    memory_tools.register(registry, memory)
    
    engine = Engine(config, memory, registry)
    
    # Test simple request
    response, trace = await engine.process("what time is it?", "test-session")
    
    print(f"✓ Planning works")
    print(f"✓ Trace: {trace.plan_steps} steps")
    print(f"✓ Success: {trace.success}")
    print(f"✓ Response: {response[:100]}...")
    
    memory.close()

if __name__ == "__main__":
    asyncio.run(test_planning())
