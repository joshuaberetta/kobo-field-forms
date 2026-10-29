# kobo-field-forms

Paper forms for [KoboToolbox](https://www.kobotoolbox.org): print a sheet from a deployed
form, fill it in by hand, scan it, read it offline, and submit the answers to Kobo. Each
page carries a QR code that ties it to the exact form version it was printed from.

**Status: proof of concept.** See [plans/poc-plan.md](plans/poc-plan.md) for the design and
milestones. Done: fetch, render (with print options), scan, read, review, submit, a
local web interface, and an end-to-end test that runs on a DigitalOcean droplet.

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
kff render <uid> --copies 20 [options]               # printable sheets (PDF)
kff batches                                          # rendered batches of sheets
kff scan scans/*.pdf photo.jpg [--debug out/dbg]      # register scanned pages
kff read --all [--fields]                            # read the answers on scanned sheets
kff sheets                                           # pages, readings, to review, submitted
kff submit 4F3A-91C2 … | --all                       # assemble and submit to Kobo
kff web [--port 8765]                                # local web interface
kff e2e [--offline] [--profiles a4-en,…] [--sheets 3]  # the whole loop on synthetic sheets
```

### Web interface

`uv run kff web`, then open http://127.0.0.1:8765/. The home page is a guide for
people using it, with screenshots (`src/kff/web/templates/home.html`; regenerate the
screenshots with `uv run python scripts/screenshots.py`, which drives the real app on a
demo store in headless Chromium). It runs on your machine only:

- **Forms** (`/forms`): fetch a project (needs `KOBO_TOKEN` in the environment `kff web` was started
  from; the browser never sees it), and list the form versions and printed batches.
- **Design sheets:** every print option, with a live preview of one sheet as you change
  them. "Answer areas from the layout map" overlays every box the scanner will read.
  "Generate PDF" renders a batch of numbered sheets (kept in `KFF_HOME/batches/`).
- **Scans:** upload scanned PDFs or photos; each page shows how it registered (the
  straightened page with the layout map drawn on it) and which pages of each sheet are
  still missing.
- **Review & submit:** read a sheet, then check its answers: each field's scan sits next
  to the value read, flagged ones first. Correct or confirm them, then submit to Kobo.

`render` writes a PDF (default `out/<uid>-<batch>.pdf`) of numbered sheets. Every page has
four corner markers and a QR code naming the form version, layout and sheet, so scanned
pages can be matched back to their form offline. `--layout-json` also writes the layout
map: the position in mm of every answer box. Questions that can't be answered on paper
(audio, video, files, map lines) are listed as warnings and printed as a note.

Print options (`kff render --help`): `--paper` (a4, a5, a3, us-letter, us-legal),
`--orientation`, `--sides double` (pads to an even page count, binding margin),
`--language en --language ar` (page language first) with `--language-layout`
`stacked` | `columns` (side by side) | `per-sheet` (separate sheets per language),
`--columns 2`, `--font-size`, `--choice-layout`, `--text-entry comb|lines`,
`--repeat-rows`, `--no-hints`, `--no-conditions`, `--no-instructions`.
Scripts (fonts, direction, digits, text entry) are configured in `src/kff/scripts.toml`.

`scan` finds each page's corner markers and QR code, straightens it, and keeps the
upright page in the store. `--debug` writes an overlay of the mapped boxes and every
field's crop.

`read` reads every answer: bubbles by fill (OMR), digits by a small offline model
(`src/kff/read/models/`, trained by `scripts/train_digits.py`), and written text by a
vision model if one is configured:

```bash
KFF_VLM_URL=http://llm:8080/v1      # any OpenAI-compatible endpoint, e.g. kobo-in-a-box's llama.cpp
KFF_VLM_MODEL=local                 # model name, if the server needs one
KFF_VLM_KEY=…                       # API key, if the server needs one
```

Without it, written text is flagged for a person to type in. Each reading has a
confidence, and anything unsure (a faint mark, two bubbles, a digit that could be
another, an impossible date) is flagged. `submit` refuses a sheet with unresolved
flags, missing required answers or broken constraints; answers to questions that skip
logic hides are dropped. The sheet id is the submission's `instanceID`, so a sheet
submitted twice is a duplicate in Kobo, not a second record.

### End-to-end test

`kff e2e` creates a throwaway `kff-e2e-*` project on your server, runs synthetic
filled-in sheets for each print profile through scan, read and submit, reads the
submissions back, and deletes the project. It fails if a scan fails, if anything Kobo
stored differs from what was sent, if resubmitting isn't a duplicate, or if bubble and
digit accuracy fall below thresholds. `--offline` skips Kobo.

On a fresh DigitalOcean droplet (asks before billing starts; destroys it afterwards):

```bash
set -a; source .env; set +a      # KOBO_TOKEN, KOBO_BASE_URL
deploy/droplet.sh all            # create, push, kff e2e, destroy
# or step by step: create | push | vlm | test | web | ssh | destroy
```

`deploy/droplet.sh vlm` downloads Gemma 4 E4B and runs it with llama.cpp on the droplet
(use `SIZE=s-8vcpu-16gb-amd`), so the test reads handwritten text too. `web` tunnels the
web interface to http://127.0.0.1:8765/. The token is piped to the droplet on stdin,
never written to a file there.

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
