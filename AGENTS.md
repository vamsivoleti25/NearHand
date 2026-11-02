# Nearhand: notes for coding agents

Nearhand schedules a small business's week from plain English and fills open shifts with part-timers
from the neighborhood. Built for the Meta Global AI Developer Hackathon (Oct 29 – Nov 4, 2026).

## Layout

- `core/` all logic, no Streamlit imports. `solver.py` (OR-Tools CP-SAT), `agent.py` (gap-filling agent,
  tool calling), `parsers.py` (the language jobs), `llm.py` (every model call: cache, cost log, retries),
  `offers.py`, `marketplace.py`, `db.py` (SQLite), `schemas.py`, `taxonomy.py`, `adapter.py`.
- `views/` one Streamlit page per file; `ui/common.py` shared UI helpers and CSS; `app.py` the shell.
- `tests/` pytest; `data/seed/` demo data; `scripts/` one-off tools; `design/BRIEF.md` the design brief.

## Rules

- The language model never assigns shifts or computes money; only `core/solver.py` does.
- All model calls go through `core/llm.py`. Always set reasoning_effort.
- Times are 15-minute integers, money is integer cents, roles and skills come from `core/taxonomy.py`.
- Money shown in Streamlit markdown goes through `ui.common.md()`.
- Run `python -m pytest -q` after every change; all tests must pass.
- Don't add dependencies without saying why. Don't edit `tests/` or `data/seed/` unless asked.
