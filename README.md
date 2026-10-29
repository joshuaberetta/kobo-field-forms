# kobo-field-forms

Paper forms for [KoboToolbox](https://www.kobotoolbox.org): print a sheet from a deployed
form, fill it in by hand, scan it, read it offline, and submit the answers to Kobo. Each
page carries a QR code that ties it to the exact form version it was printed from.

**Status: proof of concept.** See [plans/poc-plan.md](plans/poc-plan.md) for the design and
milestones. Done so far: milestone 1 (fetch).

## Setup

```bash
uv sync
cp .env.example .env    # then fill in KOBO_TOKEN (and KOBO_BASE_URL if not global)
set -a; source .env; set +a
```

## Commands

```bash
kff fetch <uid or project link> [--version <vuid>]   # deployed version → local store
kff forms [--json] [--all-servers]                   # what's in the store
kff show <uid> [--version <vuid>]                    # a stored version's form JSON
```

Run them with `uv run kff …`. The store is one SQLite file under `KFF_HOME` (default
`./data`). Only `fetch` needs the network and a token; everything after it works offline.

`fetch` always takes the **deployed** version, not the latest save: a form with undeployed
changes would otherwise print questions that submissions can't be made against.

## Development

```bash
uv run pytest            # unit tests
uv run pytest -m live    # against a real server (needs KOBO_TOKEN); read-only
uv run ruff check && uv run ruff format --check
```
