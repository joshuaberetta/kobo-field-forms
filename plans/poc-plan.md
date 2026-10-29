# kobo-field-forms — PoC plan

Status: **in progress.** Milestones 1–8 done locally, and `kff e2e` passes against kf (2026-10-29). Left: the droplet run, real paper (9), kobo-in-a-box (10).

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
- **Milestone 2b (print options): done**, after 3 so every option is tested through a
  scan. Options in `render/options.py` (all of the plan's table except `header` logo and
  `prefill`); `scripts.toml` + `scripts.py` (Latin, Arabic); `render/strings.py` (the
  sheet's own words in en, fr, es, pt, ar, ⚠ non-English unreviewed).
  - **Every profile is checked end to end** (`tests/unit/test_profiles.py`): render,
    degrade (2°, perspective, blur, JPEG), register, read a filled bubble. A4 en; Letter
    double-sided en+ar stacked; A4 landscape en+ar side by side in 2 question columns;
    A5 Arabic large print; Legal ar+en.
  - **Texts are lists of (text, lang, dir)**, one per sheet language, a repeat of the
    previous language dropped. Each is set in its own block or box with its own `lang`.
    In `stacked`, secondary languages sit under the first, aligned to the page's start
    (an Arabic line under an English label stays next to it, set right to left). Choice
    labels are inline: `Yes / نعم`.
  - **The page language (first) decides** the page direction, the sheet's own words,
    the footer and the default text entry (Arabic → lines). Skip-logic sentences are
    built in Python per language (`answer_if`, `section_if`, op phrases, question prefix
    `Q`/`P`/`س`, quotes).
  - **Double-sided** pads an odd sheet with an "intentionally blank" page (markers and QR
    too, no marks) and adds a 5 mm binding margin on the inside (`inside`/`outside`
    margins). Sheets always start on an odd page, so every sheet lays out alike.
  - **Landscape** is passed as explicit page width/height; `layout.paper` holds the
    oriented size, which registration uses.
  - **`per-sheet`** renders one layout per language (`render_set`) and merges the PDFs
    with pypdfium2.
  - **Refused up front** (`Geometry.check`): question columns under 80 mm, text rows under
    8 boxes, languages side by side under 45 mm each. **Refused after layout:** a
    question taller than a page (marks below the body), naming the question.
- **Local web interface (asked for 2026-10-29): done.** `kff web` (FastAPI + Jinja,
  vanilla JS, no build step): fetch forms, a sheet designer with every print option and
  a live one-sheet preview (72 ppi PNGs, ~0.3 s), an overlay of the layout map's answer
  areas, "Generate PDF" batches (`KFF_HOME/batches/`), and a scans page (upload, see each
  registered page with the map drawn on it, missing pages per sheet). Checked in headless
  Chromium (no console errors) and by `tests/unit/test_web.py`.
  - **Local only:** binds 127.0.0.1 and refuses any other Host header
    (`TrustedHostMiddleware`), so another site can't reach it by DNS rebinding. The Kobo
    token stays in the server's environment. `--host` elsewhere warns; for the droplet use
    an SSH tunnel.
  - This replaces the plan's milestone 7 review UI as the base: review (crops next to
    read values, edit, submit) is added to it in milestone 7.
- **Milestone 4 (synthetic filled sheets): done.** `form/xpath.py` (an evaluator for the
  usual XLSForm skip-logic/constraint XPath; anything else raises `Unsupported` and the
  rule counts as unknown), `form/rules.py` (`${name}` resolved in the asking question's
  repeat instance; group relevance), `synth/answers.py` (valid random answers that
  follow skip logic and constraints; Arabic sheets write Eastern Arabic digits half the
  time; capitals in letter boxes), `synth/fill.py` (a fill layer drawn by the same
  template: handwriting fonts with jitter and tilt per character, uneven bubble fills,
  some boxes crossed instead of filled, text on lines right to left for Arabic).
  - **The fill layer reuses the template** (`data.fill`, drawn in the foreground at the
    layout map's positions), so filled sheets are exact and need no image library.
  - Found on the way: on an Arabic page the footer's sheet code came out reversed
    (`3411-FA39`): `text(dir: ltr)` around content doesn't set the *paragraph*
    direction; `set text(dir: ltr)` inside the block does.
- **Milestone 5 (recognition): done.** `read/omr.py`, `read/digits.py` (+ `models/`,
  trained by `scripts/train_digits.py`), `read/vlm.py`, `read/sheet.py`, `bench.py`;
  `readings` table (schema 4); `kff read`, `kff sheets`.
  - **OMR:** ink share inside the bubble (crop inset 0.8 mm). Filled ≈0.95, crossed
    ≈0.3, blank 0.00; marked ≥ 0.18, empty < 0.06, between is flagged. Two marked on a
    select_one: the darker wins if 2.5× darker (flagged), else flagged as ambiguous.
  - **Found:** a box filled solid has no paper left in its crop, so estimating "paper"
    from the crop read full fills as blank (3 of 12 select_ones wrong, unflagged). The
    paper level now has a floor of 180.
  - **Digits: HOG (324) + 14×14 pixels + ink-box size → a 2-layer `cv2.ml.ANN_MLP`**, 20
    classes (Western and Eastern Arabic), each mapped to its ASCII value; the sheet's
    `digit_sets` (from `scripts.toml`, in the layout) decide which classes count. No
    PyTorch: the model is ~1 MB in the repo and runs anywhere OpenCV does.
    **OpenCV 5 dropped `cv2.ml` and `HOGDescriptor`**, so OpenCV is pinned `<5`.
  - Training data: MNIST (real handwriting) + Noto Sans / Noto Naskh renders, augmented.
    The synthetic sheets' handwriting fonts are kept out, so bench numbers aren't the
    training set. ⚠ Eastern Arabic digits have no real handwriting in training yet.
  - **Text: any OpenAI-compatible vision model** (`KFF_VLM_URL`), one call per field: a
    strip of the letter boxes, or the lines area, with the question label, languages and
    "transcribe, don't translate"; JSON reply `{text, confidence}`. Without it, written
    text is flagged (never guessed). Blank areas are detected by inked-pixel count, not
    share (a short name on a full-width line is < 1% of it).
  - **Readings a person reviewed are never overwritten** by a re-read.
  - **OpenCV's `ANN_MLP` was replaced by a numpy MLP** (522 → 256 ReLU → 20 softmax,
    Adam, 20 epochs, 509 KB): `cv2.ml` reached 93.9% on the MNIST test set, the numpy
    one **98.7%** (99.1% held out). Inference is two matrix products.
  - **Measured on synthetic sheets** (`kff e2e --offline`, 3 sheets × 4 profiles, the
    handwriting fonts unseen in training, scans degraded): bubbles, acknowledge,
    dates, times, decimals, geopoints and integers 100% on Latin sheets. On Arabic
    sheets Eastern Arabic digits read worse (60–90% per type: Naskh renders aren't Ruqaa
    handwriting), **but every wrong digit reading was flagged**: wrong-and-unflagged was
    0.0% in every profile. Without a vision model, text is only "blank" or "flagged".
  - **Found:** printed box outlines and ruled lines counted as writing in lines/box
    areas (a blank signature box read as signed). Long straight strokes (≥ 5 mm) are now
    removed before counting ink; areas are cropped at their edge.
  - **Found (generator):** on Arabic pages crosses were drawn beside the box: a bare
    `place()` anchors at the *start* edge, which is the right in RTL. The reader was
    right; the ground truth was wrong.
- **Milestone 6 (assemble + submit): done.** `submit.py`, `kobo/deploy.py`,
  `KoboClient.submit`; `submissions` table; `kff submit`.
  - Assembly drops answers to questions skip logic hides (repeatedly, as one hidden
    answer can hide another), flags missing required answers and broken constraints
    (`form/xpath.py`), and leaves out repeat entries nobody filled in.
  - XML as kobo-mcp's `build_xml`: root = asset uid with `version="<count> (<date>)"`,
    `formhub/uuid`, groups nested, one element per repeat entry, `__version__` = version
    uid, `meta/instanceID` = `uuid:<sheet id>`. Image answers (signatures) are the box's
    crop as a PNG part.
  - **Verified on kf** (`tests/live/test_live_submit.py`): create + deploy a `kff-e2e-*`
    project, submit (201), resubmit (202 duplicate), read back: every value equal, the
    attachment present; the project is deleted after.
- **Milestone 7 (review UI): done**, in the web interface: "Review & submit" lists
  scanned sheets (pages, read, to check, submitted); a sheet page shows every field's
  scan next to its reading, problems first, editable, with "checked" ticks; "Read
  again" keeps reviewed answers; "Submit" is disabled until nothing needs checking.
  The web app takes the token only from its own startup environment.
- **Milestone 8 (droplet e2e): built.** `kff e2e` (`e2e.py`), `Dockerfile`,
  `compose.yaml` (+ `vlm` profile: llama.cpp with Gemma 4 E4B), `deploy/droplet.sh`
  (`create | push | vlm | test | web | ssh | destroy | all`; asks before billing; SSH
  from your IP only; the token piped on stdin). The e2e fails on any scan failure, any
  difference between what was sent and what Kobo stored, a resubmission that isn't a
  202, or bubble/digit accuracy under `THRESHOLDS`.
  - **Passed against kf** (2026-10-29, `kff e2e --sheets 2`, ~12 s): 8 sheets in 4
    profiles submitted; every stored value equals what was sent; resubmission 202; the
    project deleted. `--accept-flagged` (auto mode) submits as read, flags and missing
    answers included, so the comparison shows what was missed.
  - **Found:** kf returned a transient 502 to a submission. Submissions are now retried
    on 5xx like GETs: the instanceID makes a repeat a duplicate, never a second record.
  - **Passed on a droplet** (2026-10-29, `s-8vcpu-16gb-amd`, ams3, 12 sheets × 4 profiles
    each run), without and with the vision model (Gemma 4 E4B Q4_0 + mmproj, llama.cpp
    CPU): 0 values differ in Kobo, resubmission 202, project deleted. 28 s without the
    model, 337 s with it.
    - Text correct: without the model 13–60% (blank ones only; the rest flagged); with it
      67–87%. Bubbles and digits as before.
    - **But the model's mistakes are confident:** wrong-and-unflagged went from 0% to
      3–6% of all fields. In letter boxes it drops thin letters (`OTIENO` → `OTENO`) and
      reads a gap between boxes as a space (`MENSAH` → `MENS AH`); on Arabic lines it
      returns plausible wrong words (`مصدر الماء بعيد` → `رضا داود بعيد`). Multi-line
      comments came back empty (flagged, so safe).
    - **Fixed the same day** (`read/sheet.py`: `box_pattern`, `fit_to_boxes`): the prompt
      gives the model the boxes' pattern (`#### ######`); the spaces come from the empty
      boxes, not the model; a reading with the wrong letter count is flagged; text on
      lines is always flagged; the model's confidence only lowers trust. **Re-run on a
      fresh droplet: wrong-and-unflagged 0.0% in every profile, and text correct went
      up to 73–87%** (re-spacing repaired `MENS AH`); 362 s.
  - Found on the way: this network blocks outgoing port 22, so `deploy/droplet.sh` has
    `SSH_PORT=443` (as kobo-in-a-box), and waits for the droplet's IP.
- **Homepage (asked for 2026-10-29): done.** `/` is a guide for users in kobo-dash's
  (and kobo-mcp's) homepage style, its CSS, fonts and logos copied into
  `web/static/site/`; the forms list moved to `/forms`. Screenshots in
  `web/static/home/` are the real app on a demo store with synthetic sheets, made by
  `scripts/screenshots.py` (Playwright, a dev dependency, pinned to kobo-mcp's 1.64.0 so
  it shares the installed Chromium). The guide states the limits plainly: unreviewed
  translations, Eastern Arabic digits, text needing a vision model.

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
    web/                  FastAPI app: designer, preview, batches, scans (later: review)
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
