# kobo-field-forms

A PoC for paper data collection with KoboToolbox: render a printable PDF (Typst) from a
deployed form, with a QR code per page referencing the form version; scan the filled-in
sheets, read them offline (OMR, digits, a vision model for text), review, and submit over
OpenRosa. Python 3.12, uv, `kff` CLI.

## Read before working

- `plans/poc-plan.md`: design, print options, scripts, milestones. **Follow it**, and keep it
  current: if it turns out wrong or ambiguous, choose the sensible option and update the
  plan in the same commit.
- Kobo API facts come from kobo-mcp's `docs/kobo-api-knowledge-base.md`
  (`~/Desktop/work/repos/kobo-mcp`). Plan references like "KB §5.6" point there. Items
  marked ⚠ there are unverified: test them live before relying on them.

## Rules

- **Tokens are secrets.** Never log, print, commit or put Kobo tokens in errors or files.
  The token only goes to the configured `KOBO_BASE_URL` host (`kobo/client.py` enforces
  this). Read `KOBO_TOKEN` / `KOBO_BASE_URL` from the env; never ask for them in chat.
- **Live tests are read-only by default.** Anything that creates projects or submissions
  on kf must only touch projects it created, named `kff-e2e-*`, and clean them up.
- **Kobo API:** KPI `/api/v2/` with trailing slashes; follow pagination `next` verbatim;
  JSON query params are JSON-stringified then URL-encoded. Build anything for submission
  from the **deployed** version's content (`versions/{deployed_version_id}/`), never the
  asset's `content`.
- **Offline first:** after `fetch`, nothing may need the network except `submit` and the
  configured VLM endpoint. No downloads at runtime (fonts and models are bundled).
- **Scripts:** script-specific behaviour (fonts, direction, digits) goes in the script
  registry, not in code branches (plan: Scripts).
- **Errors:** raise `KffError` subclasses (`errors.py`) with a message a user can act on;
  the CLI prints it, no tracebacks.

## Workflow

- Branch per milestone (`milestone-1`, …), commit in logical steps, don't push unless asked.
- `uv sync`; tests `uv run pytest` (unit) and `uv run pytest -m live` (needs `KOBO_TOKEN`);
  lint `uv run ruff check && uv run ruff format --check`.
