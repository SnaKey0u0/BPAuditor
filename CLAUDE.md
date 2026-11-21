## Agent skills

### Issue tracker

Issues and specs live in this repo's GitHub Issues (`SnaKey0u0/BPAuditor`), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Checks and landing

- Tests and lint: `tools/check.sh` (ruff + `.venv/Scripts/python -m pytest -q`). Bare `python` is miniconda, which has no pytest.
- Land a feature branch on `dev` with `tools/land.sh`. It merges, runs the checks, closes the issue and deletes the branch. Hooks live in `.githooks/`; a fresh clone needs `git config core.hooksPath .githooks`.
- Review diffs against `CODING_STANDARDS.md`. Release steps: `docs/agents/release.md`.
