# Coding standards

Review-time rules: apply each one to every diff. Mechanical rules live in `tools/check.sh` (ruff, pytest) and `tools/land.sh` (removed asserts), not here.

## Tests keep their guard

A diff that deletes or loosens an existing assertion names the test that now guards that behaviour. With no such test, the assertion stays. `tools/land.sh` stops when a test file loses more `assert` lines than it gains; this rule also covers what that count misses: a removal hidden by an unrelated addition, and loosened assertions (`==` → `in`, a narrower `pytest.raises`, a deleted test function).

## Domain terms match the docs

Code, UI strings, comments and issues use the terms in `GLOSSARY.md` with the meaning written there. A change to a rule GLOSSARY or an ADR in `docs/adr/` states (polling timings, key switching, PUUID handling) updates that doc in the same branch.

## Pure modules stay Qt-free

`key_manager.py`, `scheduler.py`, `card_view.py`, `rotation.py`, `session.py`, `analysis.py` and the other modules whose docstring says 不碰 Qt import nothing from PySide6 and start no threads or timers. `gui.py` holds the Qt wiring and carries out the actions they return.
