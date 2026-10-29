# kobo-field-forms — PoC plan

Status: **in progress.** Milestones 1, 2 and 3 done (2026-10-29, branches `milestone-1` … `milestone-3`).

Progress:
- **Milestone 1 (skeleton + fetch): done.** `kff fetch | forms | show`, SQLite store
  (`store.py`), sync KPI client ported from kobo-mcp (`kobo/client.py`), CI. Live tests
  (`tests/live/`) pass on kf, including a project whose latest save isn't deployed: fetch
  takes the deployed version's content.
  - Found: the asset list and detail both carry `deployed_version_id`, so fetch needs only
    two requests (asset, then `versions/{deployed_version_id}/`). A version detail has
    `version_number` and `date_deployed` (null for an undeployed save), which the
    submission's root `version` attribute will need (milestone 6).
  - Changed from the plan: the store keeps the form content as JSON in SQLite rather than
    as separate files: it's small, and one file is easier to copy to an offline box.
- **Milestone 2 (render): done.** `kff render` / `kff batches`; `form/model.py` (content →
  printable model), `render/form.typ`, `render/pdf.py`, `render/options.py`. Every
  printable type from the table, groups, repeats (one block per instance), skip logic in
  words, A4/A5/A3/Letter/Legal, any one language. Tested on two real kf forms too.
  - **Typst comes from the `typst` Python package** (typst-py 0.15.0, embedding Typst 0.15),
    pinned in `uv.lock`, instead of the CLI: no binary to install in CI or Docker, and
    `Compiler.query` gives the `<mark>` metadata directly.
  - **Two passes:** one placeholder sheet first (page count, positions, layout hash), then
    all sheets with their QR codes. The QR, markers and title are drawn in the page
    *foreground*, so they can't move the body; the second pass checks that every sheet's
    marks equal the first's.
  - **Checked with OpenCV at 200 ppi** (`tests/unit/test_render.py`): all four ArUco markers
    (DICT_4X4_50, ids 0–3 clockwise from top-left) found within 0.3 mm of the map (0.08 mm
    measured), every QR decodes to its page, and every box's top edge is dark at its mapped
    position with a blank inside.
  - **QR:** level Q, ~120 bytes, 22 mm, version 7 (≈0.4 mm modules: ~3 px at 200 dpi, ~5 at
    300). Sheet ids are uuid4 hex; the printed code is the first 8 (`4F3A-91C2`).
  - **Golden test** is the layout map JSON (`tests/fixtures/golden/`), not page images:
    exact, small, and readable in a diff. The OpenCV checks above cover what images would.
  - **Widget defaults:** integer digits from the constraint's upper bound (`. <= 120` → 3),
    else 4; decimals 2 places; range from `end`; text one row of comb boxes across the
    page (23 on A4), `multiline` 4 lines; signature/draw images a 30 mm box, other images
    60 mm. Choice lists: one line if ≤5 short labels, else 3 or 2 columns.
  - **Right to left comes almost free** (found while checking `--language ar`): with
    `text(lang: "ar")` Typst mirrors the grids itself (question numbers on the right,
    choice columns flipped, bubbles right of their labels), and the layout map follows:
    all 159 boxes land where mapped (`test_arabic_pages_mirror_but_marks_stay_true`).
    Digit combs stay left to right because they're explicit `stack(dir: ltr)`.
    Left for 2b: the template's own words ("Answer only if…", "Sheet", the instructions)
    need translating per language; Arabic text answers should use lines, not combs (script
    registry); the condition sentence mixes directions.
  - **Glyph gap:** Noto Sans has no `↳`; with `fallback: false` a missing glyph shows as
    a box rather than silently using another font. Kept that way: it shows up in review.
- **Milestone 3 (register + extract): done.** `kff scan FILES… [--debug DIR]`;
  `scan/load.py` (PDF via pypdfium2, images via OpenCV), `scan/register.py`,
  `scan/extract.py`, `scan/pipeline.py`, `synth/degrade.py`; a `scans` table (schema 3)
  and upright page PNGs under `KFF_HOME/scans/`.
  - **Accuracy** (`tests/unit/test_scan.py`, the 2-page fixture at 200 ppi): clean,
    rotated 3°, skewed with perspective, upside down, 150 dpi blurry JPEG, and a "phone
    photo" (5% perspective, 4°, blur, noise, JPEG 70) all register with the *true* error
    at every answer area ≤ 0.16 mm, 20–40 ms a page. A filled bubble reads ~0.97 ink,
    blank boxes 0.00, so OMR thresholds have a wide margin.
  - **The QR payload changed** from the plan's `kff1|server|asset|version|hash|sheet|p/N`
    (113 bytes, version 9, 0.39 mm modules) to **`KFF1:<HASH>:<SHEET>:<p>/<N>`** (48
    characters, all QR-alphanumeric, version 5 at level Q) printed at **25 mm** (≈0.6 mm
    modules). Measured: the long payload at 22 mm decoded in 0–2 of 3 blur levels at
    100–150 dpi; the short one at 26 mm in 3/3 down to 100 dpi. The layout hash finds the
    form version in the store, and the footer still prints the asset and version uids for
    people. `|` isn't QR-alphanumeric, so the separator is `:`.
  - **zxing-cpp** reads the QR first (it decodes blurrier codes than OpenCV's detector),
    OpenCV second. If neither reads the whole scan, the page is warped upright from the
    markers (trying each paper size by aspect ratio) and the QR corner read at 12 px/mm.
  - **Homography** from all 16 marker corners (≥3 markers needed); a mean corner error
    above 1 mm is refused rather than read from the wrong places.
  - **Photocopies share a sheet id**, so two filled-in copies would be one submission
    (Kobo answers the second with 202 duplicate). `kff scan` notes any sheet the store
    didn't print. Tell people not to photocopy sheets; print more instead.
  - A rescan of the same sheet page replaces the earlier scan (kept, not current).

Decided (2026-10-29):
- **e2e runs against kf.kobotoolbox.org** with a test account.
- **It must be able to run fully offline**, e.g. on a kobo-in-a-box laptop
  (`~/code/kobo-in-a-box`) against its local Kobo and local models, but it doesn't have to.
  Online and offline use the same code: only the Kobo URL and the recognizer endpoint change.
- **The print layout is configurable:** one or more languages per page, single- or
  double-sided, paper size and more (see [Print options](#print-options)).

Asked for: generate a printable PDF (Typst) from a deployed KoboToolbox form, with a QR code that ties
each sheet to the form version. The sheets are printed, filled in by hand, scanned, read offline
(OCR/OMR), and the results submitted to Kobo. The whole loop must be testable end to end on a
DigitalOcean droplet.

## Does it make sense?

Yes, with two adjustments:

1. **The QR code holds a reference, not the form.** A QR code holds at most ~2.9 KB, and that
   size is fragile once printed and scanned. A small form's JSON is already bigger than that. Put
   a short reference in it (server, asset uid, deployed version uid, layout hash, sheet id,
   page n/N) and keep the full form and layout map in a local store keyed by that reference.
   The store also works offline: the scan side never needs the network until submit.
2. **The paper form is designed to be machine-read.** Reading free handwriting is the weak
   link. Lay out each question type so it can be read reliably, and send anything below a
   confidence threshold to a human reviewer, who sees the image crop next to the value.

| Kobo type | On paper | How it's read | Reliability |
|---|---|---|---|
| `select_one`, `select_multiple` | a bubble or box per choice | OMR: fill ratio in the box | high |
| `integer`, `decimal` | one comb box per digit (+ a fixed decimal point) | digit classifier per box | high |
| `date`, `time` | `DD / MM / YYYY` comb boxes | digit classifier | high |
| `text` (short) | comb boxes for letters, or a lined box | HTR + review | medium |
| `text` (long, notes) | a lined area | HTR draft, always reviewed | low |
| `geopoint`, `image`, `audio`, signature | lat/lon comb boxes; image/signature submitted as a **crop** attachment | crop | — |
| `calculate`, `note`, hidden, `start`/`end` | not printed (calculations computed at submit) | — | — |
| groups / repeats | headed sections; a repeat prints as a table of N rows (N = `repeat_count` or a print option) | — | — |
| `relevant` (skip logic) | printed as instructions ("If *Yes*, answer 5–7") | values on non-relevant questions are dropped at submit | — |

## How it fits together

```
 Kobo ──fetch──▶ form store ──render──▶ PDF (N sheets, unique sheet ids) ──print──▶ paper
 (asset +        (content,               │                                          │ fill in
  deployed        layout map,            └─ layout map JSON (typst query)          ▼
  version)        sheet registry)                                                  scan
                       ▲                                                            │
                       │            ┌──────────── scan pipeline (offline) ◀────────┘
                       │            │ 1. decode QR → form ref + sheet id + page
                       └────────────┤ 2. find the 4 corner markers → homography → deskew
                                    │ 3. crop each field from the layout map
                                    │ 4. OMR / digits / HTR → value + confidence
                                    │ 5. group pages by sheet id, check constraints
                                    ▼
                               review UI (low-confidence and invalid fields)
                                    │
                                    ▼
                    submit: OpenRosa XML → POST {kpi}/submission ──▶ Kobo
```

### Key decisions

- **The layout map comes from Typst itself.** Each answer box emits a
  `#metadata((name, kind, page, x, y, w, h))<field>` from a `context` block using
  `here().position()`, and `typst query form.typ "<field>" --field value` gives exact mm
  coordinates as JSON. Spiked on typst 0.14.2: it works. No guessing at positions.
- **Registration:** ArUco markers in the four corners (OpenCV `cv2.aruco`), plus the QR
  code. The homography maps scanned pixels back to page mm, so skew, scale and a bit of
  perspective (phone photos) are all handled.
- **Sheet id = submission `meta/instanceID`.** Each printed copy gets a `uuid4` at render
  time (in the QR and printed as a short code for humans). Submitting uses
  `uuid:<sheet id>` as `instanceID`, so a sheet scanned twice is a Kobo **202 duplicate**,
  not a second record (KB §5.6 in kobo-mcp). Multi-page sheets are grouped by that id.
- **Submit against the version the sheet was printed from.** The QR carries the deployed
  version uid. Build the XML from that version's content (`GET …/versions/{uid}/`), with
  root `version` and `__version__` set as kobo-mcp does (`formcontent/openrosa.py:
  build_xml`). If the form has been redeployed since, warn, and submit anyway if the field
  names still match.
- **Scanned page images as evidence:** optional. If the form has an `image` question named
  e.g. `paper_scan`, the page images are attached to it. Otherwise the images stay in the
  local store.
- **Reuse kobo-mcp, don't depend on it.** Copy the small pieces this needs (the KPI client's
  pagination/retry, `openrosa.parse` / `build_xml`, `xform.normalize` for expanding Kobo
  blocks) with a note of where they came from. kobo-mcp's client is tied to the MCP auth
  context, and the PoC shouldn't pull in the MCP SDK.

### QR payload

Short, so the modules are big enough for a blurry 100–150 dpi scan (see milestone 3's
measurements): 48 QR-alphanumeric characters, version 5, level Q, 25 mm.

```
KFF1:<LAYOUT HASH 8>:<SHEET ID 32 hex>:<page>/<pages>
```

The layout hash covers the Typst template, the render options and the form version, so
it names the form (server, asset, version) through the store's layout map, and a
re-render with different options can't be decoded with the wrong map. The footer prints
the asset and version uids for people.

**Every page carries its own QR and corner markers**, front and back. Pages can then be
scanned in any order, upside down (the markers give the orientation), or as single sheets
from a double-sided form.

## Print options

A **print profile** is a named set of options saved in the store (`kff profile create`,
or flags on `kff render`). Options are passed to the template through `sys.inputs`; the
Python side doesn't generate Typst code.

| Option | Values | Default |
|---|---|---|
| `paper` | `a4`, `us-letter`, `us-legal`, `a5`, `a3` (any Typst paper name) | `a4` |
| `orientation` | `portrait`, `landscape` | `portrait` |
| `sides` | `single`, `double` | `single` |
| `languages` | one or more of the form's translations, in order | the form's default |
| `language_layout` | `stacked` (each label in every language, one under another), `columns` (languages side by side), `per-sheet` (one copy per language, each with its own sheets) | `stacked` |
| `columns` | 1 or 2 question columns per page | 1 |
| `font_size` | e.g. `10pt`, `12pt` (large-print sheets) | `10pt` |
| `choice_layout` | `vertical`, `horizontal`, `auto` (horizontal when the labels are short) | `auto` |
| `text_entry` | `comb` (one box per letter) or `lines`, per question type or per question | `comb` for short text |
| `repeat_rows` | default rows for a repeat without `repeat_count`; can be set per repeat | 5 |
| `show` | any of `hints`, `question_numbers`, `names` (xpaths, for testing), `skip_instructions`, `sheet_code` | all but `names` |
| `header` | title, logo, instructions page (on or off) | form title, no logo |
| `prefill` | a CSV of values to print into chosen questions, one row per sheet (see Later) | none |

Notes:
- **Double-sided:** each sheet's page count is padded to an even number with a blank page
  ("intentionally left blank", still with markers and a QR), so one sheet never shares a
  leaf with the next. The binding margin alternates sides.
- **Languages and scripts:** see [Scripts](#scripts). In `stacked` and `columns` layouts
  the answer boxes are printed once, under all of the labels.
- **The recogniser gets the languages:** the profile's languages go to the text recogniser
  as a hint (see [Recognition](#stack)).
- **Validation:** `kff render` refuses impossible combinations up front, e.g. three
  languages in `columns` on A5 portrait, or a choice list too long to fit a page.

## Scripts

Early testing covers **Latin and Arabic**. Every script-specific detail lives in one
registry (`src/kff/scripts.toml`), so adding a script (Ethiopic, Devanagari, Cyrillic, …)
means adding an entry, its font files and test data, with no code changes.

```toml
[latin]
fonts = ["Noto Sans"]
direction = "ltr"
digits = ["0123456789"]
text_entry = "comb"          # separate letters work well in boxes

[arabic]
fonts = ["Noto Naskh Arabic", "Noto Sans Arabic"]
direction = "rtl"
digits = ["0123456789", "٠١٢٣٤٥٦٧٨٩"]   # Western and Eastern Arabic; Persian ۰–۹ when needed
text_entry = "lines"         # cursive: letters don't split into boxes
```

- **Language → script:** from the form's translation names. Kobo's are `"Arabic (ar)"`,
  `"English (en)"`, so take the code in brackets and map it with a small table (`ar`, `fa`,
  `ur`, `ps` → arabic; anything unknown → latin, with a warning). A print profile can
  override it.
- **Fonts are bundled** in the image (OFL-licensed Noto), so rendering works offline and
  looks the same on every machine. Typst is run with `--ignore-system-fonts` so a
  missing font fails the render instead of being replaced silently.
- **Rendering right to left** (spiked on typst 0.14.2): with `text(lang: "ar")` Typst joins
  the letters, sets the line right to left, and `typst query` still reports the box
  positions. **Each language's label must be its own block with its own `lang`:** an
  English and an Arabic label in one run puts the punctuation on the wrong side
  (`?What is your name`). An Arabic page mirrors the layout: labels on the right, choice
  bubbles to the right of their labels, columns and the binding margin flipped.
- **Numbers stay left to right**, even on Arabic pages (that's how they're written). Digit
  comb boxes accept either digit set. The digit recogniser reads both and normalises to
  ASCII before submitting, since Kobo's integer, decimal and date fields need ASCII. The
  original reading is kept for review.
- **Text answers are submitted as written**, in whatever script, as Unicode NFC.
- **Recognition per script:**
  - Digit model: trained on EMNIST digits (Western) plus an Eastern Arabic handwritten
    digits set (AHDD1 / MADBase, ⚠ licence to check), one 20-class model, or two models
    chosen by the registry's `digits`.
  - VLM: the prompt names the expected script and language. TrOCR isn't usable for Arabic,
    so without a VLM, Arabic text goes straight to review.
- **Synthetic test data per script:** Latin handwriting from EMNIST letters and
  handwriting fonts. Arabic from handwriting-style Arabic fonts (OFL, e.g. Aref Ruqaa) to
  start with, and later from real samples (KHATT and IFN/ENIT are research-only, ⚠ check
  terms). Fonts are much easier to read than real handwriting, so the synthetic accuracy is
  an upper bound. The real-paper milestone gives the real number.

## Stack

- **Python 3.12, uv, ruff, pytest** (same as kobo-mcp).
- **Typst** CLI for rendering (template in `templates/form.typ`; the form arrives as JSON
  through `sys.inputs` / `json()`, so there's no code generation of Typst source).
- **Scan side:** `opencv-python-headless` (markers, homography, crops, OMR), `pyzbar` or
  OpenCV's QR detector, `pdf2image`/`pypdfium2` for scanned PDFs.
- **Recognition:** each field kind has a `Recognizer` behind one interface, picked in
  config.
  - **OMR:** fill ratio in each box, thresholds calibrated on the blank render. Pure
    OpenCV, works anywhere.
  - **Digits:** a small CNN trained on EMNIST digits, exported to ONNX (`onnxruntime`), a few
    MB, CPU. Optionally cross-checked by the vision model when confidence is low.
  - **Handwritten text: a vision-language model over an OpenAI-compatible API**
    (`KFF_VLM_URL`, `KFF_VLM_MODEL`). The prompt gets the crop, the question label, the
    expected languages and any constraint, and must answer as JSON `{value, confidence}`.
    - **Offline:** kobo-in-a-box's llama.cpp with Gemma 4 E4B and its `mmproj` (vision
      projector), already running at `http://llm:8080/v1`. It handles many scripts, which
      matters for multi-language forms.
    - **Online:** any hosted model behind the same API, for comparison. Off unless
      configured.
    - **No model at all:** TrOCR-small-handwritten through ONNX (~250 MB, mostly Latin
      script) as a fallback, or no recogniser, so every text field goes to review.
    - The model's own confidence isn't trusted on its own. Low-confidence or invalid
      values, and values that disagree with a second reading (e.g. the digit CNN), go to
      review.
  - Tesseract only for printed text, if ever needed (it is poor on handwriting).
- **Review UI:** a small FastAPI + plain HTML page: upload scans, see each sheet's fields
  with their crops, fix values, submit.
- **Packaging:** one Docker image for x86 and ARM (typst, fonts, Python, the digit and TrOCR
  models), run with `docker compose`. The VLM is a separate service: either a `vlm` compose
  profile here (llama.cpp CPU image + Gemma 4 E4B weights) or the one already running on
  kobo-in-a-box.

### Where it runs

| Mode | Kobo | Text recogniser | Used for |
|---|---|---|---|
| **Standalone droplet** (default e2e) | kf.kobotoolbox.org | `vlm` profile on the same droplet (CPU), or none | the e2e test, demos |
| **kobo-in-a-box** (fully offline) | the box's local Kobo (`http://kf.kobo.local`) | the box's llama.cpp (`http://llm:8080/v1`) | field use; joins the box's compose network as one more service, like kobo-dash |
| **Laptop / dev** | kf | optional | development |

`KOBO_BASE_URL` takes any Kobo URL, including plain-HTTP local hosts, because the box
serves Kobo over HTTP on a closed network. Nothing calls out beyond the configured Kobo
and VLM hosts, so `offline.sh on` in kobo-in-a-box is a valid test.

## Repo layout

```
kobo-field-forms/
  CLAUDE.md
  plans/poc-plan.md
  pyproject.toml, uv.lock, Dockerfile, compose.yaml
  src/kff/
    kobo/client.py        KPI client (token from env), versions, OpenRosa submit
    form/model.py         content → printable model (questions, choices, groups, repeats)
    render/pdf.py         model → PDF + layout map (typst compile + query, two passes)
    scan/register.py      QR decode, marker detection, homography
    scan/extract.py       field crops from the layout map
    recognize/            omr.py, digits.py, htr.py (Recognizer interface)
    assemble.py           values → answers, constraints, relevance, multi-page grouping
    submit.py             answers → OpenRosa XML → /submission
    store.py              local store (SQLite + files): forms, layouts, sheets, scans, results
    web/                  FastAPI review UI
    scripts.toml          script registry: fonts, direction, digit sets, text entry
    render/form.typ       the Typst template
    render/fonts/         bundled OFL fonts (Noto Sans, Noto Naskh Arabic, …)
    cli.py                kff fetch | render | scan | review | submit | e2e
  tests/
    unit/ ...
    fixtures/forms/       XLSForms covering each question type
    synth/                synthetic "filled-in + scanned" generator (see below)
  deploy/
    droplet.sh            doctl: create, provision (cloud-init), run, destroy
    cloud-init.yaml
```

## Testing end to end on a droplet

The point of the droplet is a clean, disposable machine that runs the whole loop with no
help from a laptop.

**Synthetic fill-and-scan generator** (`tests/synth/`). It makes printing and scanning
repeatable, so the full loop can run in CI or on the droplet:
1. Render the PDF and rasterise it at 200–300 dpi.
2. Generate random but valid answers for the form (respecting constraints and skip logic;
   kobo-mcp's `formcontent/mock.py` already does this and can be ported).
3. Draw them into the boxes from the layout map: filled bubbles with a wobbly pen stroke,
   digits and text from EMNIST samples or handwriting-style fonts.
4. Degrade the image like a scanner or phone would: rotation ±3°, a little perspective,
   blur, noise, JPEG compression, a stray mark or two.
5. Keep the answers as ground truth.

**The e2e test** (`kff e2e`, run by `deploy/droplet.sh test`):
1. `doctl compute droplet create` (Ubuntu 24.04, tagged `kff-e2e`), with cloud-init
   installing Docker, then copying the repo and running `docker compose up`. Size:
   `s-2vcpu-4gb` without the VLM, `s-8vcpu-16gb-amd` with the `vlm` profile (Gemma 4 E4B
   Q4_0 is ~4.6 GB plus its projector). Follow kobo-in-a-box's `provision.sh`: confirm
   before billing starts, use its region fallback list, and its firewall.
2. On the droplet: deploy a throwaway project to Kobo from `tests/fixtures/forms/` (a name
   like `kff-e2e-<timestamp>`).
3. `fetch` → `render` 5 sheets → synthesise filled-in scans → `scan` → auto-accept fields
   (no review step) → `submit`. This runs once for each profile in a test matrix, so the
   layout options are covered end to end, not just in golden images:
   - A4, single-sided, one language;
   - Letter, double-sided, two languages `stacked`, with pages scanned shuffled and some
     upside down;
   - A4 landscape, two languages in `columns`, 2 question columns;
   - Arabic only, single-sided, A5 large print, with answers in Arabic script and Eastern
     Arabic digits.

   The fixture form has English, French and Arabic translations, plus a repeat, skip logic
   and every printable question type. Ground truth mixes the scripts: Latin answers on
   English sheets, Arabic answers and both digit sets on Arabic sheets, and some of each on
   bilingual sheets.
4. Read the submissions back (`GET …/data/`) and compare them with the ground truth. Pass if
   every OMR and digit field matches and text fields meet a character-accuracy threshold.
   Also check that resubmitting one sheet gives a 202 duplicate.
5. Write a report (per-field accuracy, confidence histogram, timings), copy it back, and
   **delete the test project** (it was made by the test, so no confirmation gate is needed;
   only delete projects named `kff-e2e-*` that the test created).
6. Destroy the droplet (always, even on failure, unless `--keep`).

**The manual loop** on the same droplet: open the review UI (behind basic auth over HTTPS
with Caddy, or through an SSH tunnel), download the PDF, print it, fill it in, scan it,
upload it, review, submit. This is the real-world check the synthetic test can't replace.

The Kobo token is passed to the droplet as an env var at run time (`KOBO_TOKEN`,
`KOBO_BASE_URL`), never written to the repo or baked into the image.

## Milestones

Each one ends with something that runs.

1. **Skeleton + fetch.** uv project, CI, KPI client, `kff fetch <uid>` saves the deployed
   version's content to the store. Live test against kf.
2. **Render.** `templates/form.typ` covering the types in the table above, corner markers
   and a QR on every page, layout map from `typst query`, `kff render <uid> --copies 5`.
   Single language, A4 and Letter, single-sided. Golden-image tests on the fixtures.
2b. **Print options.** Profiles; multiple languages (`stacked`, `columns`, `per-sheet`),
   the script registry with Latin and Arabic (bundled fonts, mirrored right-to-left pages);
   double-sided padding and alternating margins; question
   columns, font size, choice layout, comb or lines. Golden images per profile, plus
   `render` refusing impossible combinations.
3. **Register + extract.** QR decode, homography, field crops. Test: render → rasterise →
   rotate/warp → crops line up with the boxes within 1 mm.
4. **Synthetic generator.** The fill-and-degrade pipeline with ground truth.
5. **Recognition.** OMR, then digits, then text through the VLM (`vlm` compose profile),
   with TrOCR as the fallback. Each measured on the synthetic set, per language/script.
6. **Assemble + submit.** Constraints, relevance, multi-page grouping, OpenRosa XML,
   `/submission`, duplicate handling. Live test: submit and read back.
7. **Review UI.** Upload, per-field crops, edit, submit.
8. **Droplet e2e.** `deploy/droplet.sh`, cloud-init, compose, `kff e2e`, report, teardown.
9. **Real paper.** Print, fill in by hand, scan on a real scanner and a phone. Tune the
   thresholds and record the accuracy here.
10. **kobo-in-a-box.** Run as a service on the box's network against its local Kobo and
   llama.cpp, with `offline.sh on`. Repeat the e2e there (the box's Kobo instead of kf).

## Open questions

- **Gemma 4 E4B on handwriting:** unmeasured. Spike it early (milestone 5, or before) on a
  few real handwritten crops in Latin and Arabic script (and both digit sets). If it's weak, try 26B-A4B on the GPU
  tier, and check the CPU time per field: a 30-question sheet at a few seconds per text
  field is fine in batch, but not interactively.
- **Scripts after Latin and Arabic:** which come next? Each needs a registry entry, fonts
  and test data (see [Scripts](#scripts)).

## Later

- Bulk scanning with a sheet feeder: one multi-page PDF of many sheets, split by QR.
- Pre-filled sheets (e.g. a household id or name printed from a list) for follow-up visits.
- Editing an existing submission from a re-scanned sheet (`deprecatedID`).
- Running the scan pipeline in the browser or on a phone.
