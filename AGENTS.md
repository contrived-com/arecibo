# AGENTS.md

## Project Overview

Arecibo is a lightweight Python embedded agent. It provides a minimal entry point (`agent.py`) for agent functionality.

## Project Structure

- `agent.py` — Main entry point
- `requirements.txt` — Python dependencies (currently empty)
- `scripts/claudia/` — Claudia agent metadata (PRD, progress log)

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
python agent.py
```

## Conventions

- Python 3 standard style
- Virtual environment in `.venv/` (gitignored)

## Do Not Modify

- `scripts/claudia/prd.json` — Managed by Claudia task system; edit through PRD workflows, not directly
