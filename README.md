# kobo-field-forms

Paper forms for [KoboToolbox](https://www.kobotoolbox.org): print a sheet from a deployed
form, fill it in by hand, scan it, read it offline, and submit the answers to Kobo. Each
page carries a QR code that ties it to the exact form version it was printed from.

**Status: proof of concept.** See [plans/poc-plan.md](plans/poc-plan.md) for the design and
milestones. Done so far: milestones 1 (fetch) and 2 (render).

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
kff render <uid> --copies 20 [--paper us-letter] [--language ar] [--preview out/png]
kff batches                                          # rendered batches of sheets
```

`render` writes a PDF (default `out/<uid>-<batch>.pdf`) of numbered sheets. Every page has
four corner markers and a QR code naming the form version, layout and sheet, so scanned
pages can be matched back to their form offline. `--layout-json` also writes the layout
map: the position in mm of every answer box. Questions that can't be answered on paper
(audio, video, files, map lines) are listed as warnings and printed as a note.

Run them with `uv run kff …`. The store is one SQLite file under `KFF_HOME` (default
`./data`). Only `fetch` needs the network and a token; everything after it works offline.
The Typst renderer comes from the `typst` Python package (pinned in `uv.lock`), and its
fonts are bundled in `src/kff/render/fonts/`, so nothing else needs installing.

`fetch` always takes the **deployed** version, not the latest save: a form with undeployed
changes would otherwise print questions that submissions can't be made against.

## Development

```bash
uv run pytest            # unit tests (KFF_UPDATE_GOLDENS=1 rewrites the golden layout)
uv run pytest -m live    # against a real server (needs KOBO_TOKEN); read-only
uv run ruff check && uv run ruff format --check
```
