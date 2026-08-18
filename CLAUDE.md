# CLAUDE.md

Operating instructions for Claude Code in this repo. **Instructions only** — for
what the system *is*, read the docs below rather than restating them here.

## Read first

1. [README.md](README.md) — current state and how to run it
2. [PHASE_1.md](PHASE_1.md) — what we're building now
3. [DESIGN.md](DESIGN.md) — schema, data model, sanitization rules, API reference
4. [DECISIONS.md](DECISIONS.md) — why things are the way they are

## Commands

```bash
venv\Scripts\activate

python -m src.db_setup --reset && python -m src.known_types && python -m src.sync
python -m src.sync --use-cache        # reuse data/cache/, skip the API

python -m pytest tests/ -v
python -m pytest tests/test_sanitize.py::ToIntTests
```

## Conventions

- **Sanitization rules are pure.** New rules go in `src/sanitize.py` as standalone
  functions with no DB access, plus tests in `tests/test_sanitize.py`.
- **Log, don't crash.** Bad API rows go to `skipped_cards_log` or
  `unknown_type_log` — never raise on third-party data.
- **Sync is idempotent.** Running it twice must not duplicate anything.
- **Update docs in the same commit as the behavior they describe.**
- **`DECISIONS.md` is append-only.** Never edit an entry; supersede it.
- Commit messages: short imperative summary, no body.

## Gotchas

- `data/optcg.db` is gitignored and always regenerable — never treat it as precious.
- The API lags the real game (ends at OP16; the game is at OP17). Not a bug.
- New card types appear with new sets. When `unknown_type_log` is non-empty after
  a sync, add them to `SEED_TYPES` in `src/known_types.py` and re-sync.
