# ATLAS — Autonomous Task Layer & Agent System

**A Jarvis-level AI system that plans, executes, and learns.**

```
┌─────────────────────────────────────────────────────────────┐
│  ATLAS — Your intelligent autonomous assistant              │
│  • Plans before acting                                      │
│  • Learns your preferences                                  │
│  • Automates complex workflows                              │
│  • Safe, traceable, controllable                            │
└─────────────────────────────────────────────────────────────┘
```

## What Makes ATLAS Different

### 🧠 Intelligent Planning
ATLAS doesn't just react—it **plans**. Every request is broken down into 2-5 concrete steps before execution.

```
you: research Python and save to file

ATLAS creates plan:
  1. web_search("Python best practices")
  2. write_file("research.md", results)

Then executes with full tracing
```

### 🔍 Full Execution Visibility
See exactly what ATLAS is doing, step by step:

```
╭─────────────── Execution Trace ────────────────╮
│ Goal: Research and save                        │
│ ✓ Step 1: web_search → Found 8 results        │
│ ✓ Step 2: write_file → Written 1234 chars     │
│ Duration: 2341ms | Success: True               │
╰────────────────────────────────────────────────╯
```

### 🧩 Smart Memory
ATLAS remembers what matters:
- **Importance scoring** - Preferences > Decisions > Facts
- **Auto-deduplication** - No redundant memories
- **Context-aware retrieval** - Finds relevant information

### 🛡️ Hardened Safety
- 30+ blocked dangerous patterns
- 3-tier approval system (AUTO/NOTIFY/CONFIRM)
- No API key exfiltration
- Traceable actions

### 🔧 Powerful Tools
- **Filesystem** - Read, write, search files
- **System** - Shell commands, system info
- **Web** - Search, fetch content
- **Browser** - Playwright automation
- **Memory** - Long-term knowledge
- **Workflows** - Reusable task sequences
- **Scheduler** - Background tasks

## Quick Start

```bash
# Install
pip install -e .

# Run
atlas

# Try it
you: what time is it?
you: search for Python best practices
you: remember I prefer TypeScript
```

## Installation

### Basic
```bash
git clone <repo>
cd atlas
python -m venv .venv
source .venv/bin/activate  # or `. .venv/bin/activate` on macOS
pip install -e .
```

### With Browser Automation
```bash
pip install -e ".[browser]"
playwright install chromium
```

### Configuration
Create `.env`:
```bash
ATLAS_GEMINI_API_KEY=your-key-here
ATLAS_MODEL=gemini-2.5-flash
```

## Features

### 1. Intelligent Planning
- Structured task decomposition
- Tool selection without hallucination
- Step-by-step execution
- Full execution tracing

### 2. Enhanced Memory
- Importance-weighted storage
- Automatic deduplication
- Type categorization (fact, preference, decision, task)
- FTS5 full-text search

### 3. Workflow System
- YAML/JSON workflow definitions
- Variable substitution
- Error handling (stop/continue/retry)
- Reusable task sequences

### 4. Background Scheduler
- Delayed task execution
- Recurring tasks (cron-like)
- SQLite persistence
- Enable/disable tasks

### 5. Browser Automation
- Open URLs
- Take screenshots
- Extract content
- Click elements
- Fill forms

### 6. Hardened Safety
- 30+ blocked dangerous patterns
- 3-tier approval system
- Shell command allowlist
- No destructive actions without approval

## Architecture

```
User Input
    ↓
Planner (Gemini 2.5) → Structured Plan (JSON)
    ↓
Executor → Step-by-step execution
    ↓
Tools (Filesystem, System, Web, Browser, Memory)
    ↓
Memory Store (SQLite + FTS5)
    ↓
Scheduler (Background Tasks)
```

## Usage Examples

### File Management
```
you: find all Python files and count lines
you: search for TODO in my code
you: backup config files to ~/backup
```

### Web Research
```
you: search for latest AI news
you: get content from URL and summarize
you: take screenshot of github.com
```

### System Tasks
```
you: what's my disk usage
you: show running processes
you: install package with pip
```

### Memory & Learning
```
you: remember I prefer tabs over spaces
you: what do you know about my preferences?
you: save this as an important decision
```

### Workflows
Create `.atlas/workflows/research.yaml`:
```yaml
name: "research-and-save"
steps:
  - tool: "web_search"
    params: {query: "{{topic}}"}
  - tool: "write_file"
    params:
      path: "{{output}}"
      content: "{{step_0_result}}"
```

Run: `you: run research workflow for machine learning`

### Automation
```
you: every day at 9am, backup my documents
you: remind me in 2 hours
you: run tests when I save Python files
```

## CLI Commands

```
/help          Show all commands
/quit          Exit ATLAS
/cost          Show API costs
/memories      List stored memories
/remember X    Save memory manually
/clear         Clear screen
/new           New session
```

## Safety Features

### Approval Tiers
- **AUTO** - Runs silently (reads, searches)
- **NOTIFY** - Runs but notifies (writes, saves)
- **CONFIRM** - Asks permission (deletes, shell, browser actions)

### Blocked Patterns
```
rm -rf /, sudo rm, mkfs, dd of=/dev,
curl | bash, API_KEY exfiltration,
/etc/passwd access, and 20+ more
```

### Allowlist
Safe commands run without confirmation:
```
ls, cat, grep, git, npm, pip, brew, open
```

## Project Structure

```
atlas/
├── core/
│   ├── engine.py       # Main orchestration
│   ├── planner.py      # Task planning
│   ├── executor.py     # Step execution
│   ├── trace.py        # Execution tracing
│   ├── models.py       # Data models
│   └── approval.py     # Safety checks
├── tools/
│   ├── filesystem.py   # File operations
│   ├── system.py       # Shell & system
│   ├── web.py          # Web search & fetch
│   ├── browser.py      # Playwright automation
│   ├── memory_tools.py # Memory access
│   └── registry.py     # Tool management
├── memory/
│   └── store.py        # SQLite + FTS5
├── workflows/
│   ├── engine.py       # Workflow execution
│   ├── loader.py       # YAML/JSON loading
│   └── models.py       # Workflow models
├── scheduler/
│   ├── scheduler.py    # Task scheduling
│   └── models.py       # Task models
└── interfaces/
    ├── cli.py          # Rich-powered CLI
    └── voice.py        # (Future) Voice interface
```

## Performance

- Simple tasks: ~1-2s
- Complex tasks: ~3-5s
- Planning overhead: ~500ms
- Browser operations: ~2-4s

## Roadmap

### ✅ Completed
- [x] Intelligent planning layer
- [x] Execution tracing
- [x] Enhanced memory system
- [x] Workflow engine
- [x] Background scheduler
- [x] Browser automation
- [x] Hardened safety

### 🚧 In Progress
- [ ] Voice interface (push-to-talk)
- [ ] Workflow marketplace
- [ ] Memory importance auto-learning

### 📋 Planned
- [ ] Multi-agent coordination
- [ ] Plugin system
- [ ] Web dashboard
- [ ] Mobile app

## Documentation

- [QUICKSTART.md](QUICKSTART.md) - Get started quickly
- [EVOLUTION.md](EVOLUTION.md) - Detailed evolution from v1
- `.env.example` - Configuration template

## Requirements

- Python 3.11+
- Gemini API key
- Optional: Playwright for browser automation
- Optional: Deepgram/OpenAI for voice (future)

## Contributing

ATLAS is designed for:
- **Reliability** - No hallucinations, traceable actions
- **Intelligence** - Plans before acting
- **Control** - Full visibility, approval system
- **Power** - Rich tool ecosystem

Every feature must improve one of these pillars.

## License

MIT

## Credits

Built with:
- Google Gemini 2.5 Flash
- Rich (CLI)
- Playwright (Browser)
- SQLite + FTS5 (Memory)
- Pydantic (Models)

---

**ATLAS: Your intelligent autonomous assistant.**

Not a chatbot. Not a demo. A real system.

```bash
atlas
```
