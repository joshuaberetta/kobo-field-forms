// A printable Kobo form. All data arrives as JSON in sys.inputs.data (see render/pdf.py):
// strings are inserted as text, never evaluated as markup.
//
// Every answer area emits `#metadata(...)<mark>` at its top-left corner, and every page its
// corner markers and QR as `<page-mark>`, so `typst query` gives the scanner an exact layout
// map in mm. Positions are relative to the page's top-left corner.
//
// Texts come as lists of (text, lang, dir), one per sheet language. Each is set in its own
// box with its own `lang`, never joined into one run: mixing directions in a run puts the
// punctuation on the wrong side.

#let data = json(bytes(sys.inputs.data))
#let opts = data.options
#let model = data.model
#let geo = data.geometry
#let strings = data.strings
// Pages per sheet: 0 in the first pass, which renders one sheet to measure it.
#let per-sheet = data.pages_per_sheet
#let side-by-side = opts.language_layout == "columns"

#let ink = rgb("#555555")
#let faint = rgb("#999999")
#let red = rgb("#b00020")
#let stroke-w = 0.6pt
#let cell-w = geo.cell_w * 1mm
#let cell-h = geo.cell_h * 1mm
#let cell-gap = geo.cell_gap * 1mm
#let bubble = geo.bubble * 1mm

#set document(title: model.title)
// Fonts are tried in order per glyph; `fallback: false` keeps out system fonts, so a
// missing glyph shows as a box instead of silently changing font.
#set text(font: data.fonts, size: opts.font_size * 1pt, fallback: false)
#set text(lang: data.lang) if data.lang != none
#set par(leading: 0.55em, spacing: 0.9em)

// -- marks ---------------------------------------------------------------------

#let mark(info) = context {
  let pos = here().position()
  [#metadata(info + (page: pos.page, x: pos.x.mm(), y: pos.y.mm()))<mark>]
}

// A box whose top-left corner is recorded. `info` says what the box is.
#let marked-box(info, width: 0pt, height: 0pt, ..style) = box(
  width: width,
  height: height,
  ..style,
  place(top + left, mark(info + (w: width.mm(), h: height.mm()))),
)

// -- text in one or more languages ---------------------------------------------------

#let lang-of(t) = if t.lang != none { t.lang } else if data.lang != none { data.lang } else { "en" }
#let dir-of(t) = if t.dir == "rtl" { rtl } else { ltr }
#let one(t, ..style) = text(lang: lang-of(t), dir: dir-of(t), ..style, t.text)

// Block-level: languages stacked, or side by side with `columns`. The first language is
// the page's; the others are a shade lighter.
#let multi(texts, after: none, ..style) = {
  let parts = texts.enumerate().map(((i, t)) => block(above: 0.5mm, below: 0mm, {
    set text(lang: lang-of(t), dir: dir-of(t))
    if i == 0 { one(t, ..style) } else { one(t, fill: ink, ..style) }
    if i == 0 and after != none { after }
  }))
  if side-by-side and texts.len() > 1 {
    grid(columns: (1fr,) * texts.len(), column-gutter: 4mm, ..parts)
  } else {
    stack(dir: ttb, spacing: 0.8mm, ..parts)
  }
}

// Inline (choice labels): each language in its own box, separated by " / ".
#let inline(texts) = {
  for (i, t) in texts.enumerate() {
    if i > 0 { text(fill: faint, " / ") }
    box(if i == 0 { one(t) } else { one(t, fill: ink) })
  }
}

// -- page furniture: corner markers, QR code, title, footer --------------------------

#let sheet-of(abs) = if per-sheet > 0 { calc.quo(abs - 1, per-sheet) } else { 0 }
#let page-in-sheet(abs) = if per-sheet > 0 { calc.rem(abs - 1, per-sheet) + 1 } else { abs }
#let fill-in(template, ..pairs) = {
  let out = template
  for (k, v) in pairs.named() { out = out.replace("{" + k + "}", str(v)) }
  out
}

#let marker(bits, size) = {
  let n = bits.len()
  let m = size / n
  box(width: size, height: size, {
    for (r, row) in bits.enumerate() {
      for (c, bit) in row.enumerate() {
        if bit == 0 { place(top + left, dx: c * m, dy: r * m, rect(width: m, height: m, fill: black, stroke: none)) }
      }
    }
  })
}

#let furniture = context {
  let abs = here().page()
  let s = sheet-of(abs)
  let p = page-in-sheet(abs)
  let w = data.paper.w
  let h = data.paper.h
  let size = geo.marker_size
  let off = geo.marker_offset
  let corners = (
    (id: 0, x: off, y: off),
    (id: 1, x: w - off - size, y: off),
    (id: 2, x: w - off - size, y: h - off - size),
    (id: 3, x: off, y: h - off - size),
  )
  for c in corners {
    place(top + left, dx: c.x * 1mm, dy: c.y * 1mm, marker(data.markers.at(c.id), size * 1mm))
    place(top + left, dx: c.x * 1mm, dy: c.y * 1mm,
      [#metadata((k: "marker", id: c.id, page: abs, x: c.x, y: c.y, w: size, h: size))<page-mark>])
  }
  // The QR code sits left of the top-right marker, whatever the page direction.
  let q = geo.qr_size
  let qx = w - off - size - geo.qr_gap - q
  let qy = off
  let sheet = if s < data.sheets.len() { data.sheets.at(s) } else { none }
  let svg = if sheet != none and p <= sheet.qr.len() { sheet.qr.at(p - 1) } else { none }
  place(top + left, dx: qx * 1mm, dy: qy * 1mm,
    if svg != none { image(bytes(svg), width: q * 1mm, height: q * 1mm) }
    else { rect(width: q * 1mm, height: q * 1mm, stroke: 0.5pt + faint) })
  place(top + left, dx: qx * 1mm, dy: qy * 1mm,
    [#metadata((k: "qr", page: abs, x: qx, y: qy, w: q, h: q))<page-mark>])
  [#metadata((k: "page", page: abs, sheet: s, sheet_page: p))<page-mark>]

  // Synthetic answers (synth/fill.py), drawn only for test scans: never in a printed PDF.
  for f in data.at("fill", default: ()).filter(f => f.page == p) {
    let pen = rgb(f.pen)
    let at(body) = place(top + left, dx: (f.x + f.dx) * 1mm, dy: (f.y + f.dy) * 1mm, body)
    if f.k == "char" {
      at(box(width: f.w * 1mm, height: f.h * 1mm, align(center + horizon,
        rotate(f.rot * 1deg, text(font: f.font, size: f.size * 1pt, fill: pen, fallback: true, f.text)))))
    } else if f.k == "blob" {
      let cx = f.w / 2 - f.rx
      let cy = f.h / 2 - f.ry
      at(place(top + left, dx: cx * 1mm, dy: cy * 1mm,
        ellipse(width: 2 * f.rx * 1mm, height: 2 * f.ry * 1mm, fill: pen, stroke: none)))
    } else if f.k == "cross" {
      // `top + left` explicitly: a bare place() anchors at the start edge, which is the
      // right on an Arabic page.
      at({
        place(top + left, line(start: (0.5mm, 0.5mm), end: ((f.w - 0.5) * 1mm, (f.h - 0.5) * 1mm), stroke: 1.3pt + pen))
        place(top + left, line(start: ((f.w - 0.5) * 1mm, 0.5mm), end: (0.5mm, (f.h - 0.5) * 1mm), stroke: 1.3pt + pen))
      })
    } else if f.k == "text" {
      at(box(width: (f.w - 2 * f.dx) * 1mm, height: f.h * 1mm,
        align(if f.dir == "rtl" { right } else { left },
          rotate(f.rot * 1deg, origin: left + top, text(font: f.font, size: f.size * 1pt, fill: pen,
            dir: if f.dir == "rtl" { rtl } else { ltr }, lang: if f.dir == "rtl" { "ar" } else { "en" },
            fallback: true, f.text)))))
    }
  }

  // Title block, between the top-left marker and the QR code.
  let tx = off + size + geo.qr_gap
  place(top + left, dx: tx * 1mm, dy: off * 1mm, block(width: (qx - tx - geo.qr_gap) * 1mm, {
    text(size: 1.25em, weight: "bold", model.title)
    linebreak()
    text(size: 0.8em, fill: ink, {
      [#strings.sheet ]
      if sheet != none { strong(sheet.code) } else [----]
      [ · #fill-in(strings.page_of, p: p, n: calc.max(per-sheet, 1))]
    })
  }))

  // Footer, between the bottom markers. Identifiers read left to right.
  place(top + left, dx: tx * 1mm, dy: (h - off - size / 2 - 2) * 1mm,
    block(width: (w - 2 * tx) * 1mm, {
      // The paragraph's own direction, not just the text's: else on an Arabic page the
      // codes come out reversed (`3411-FA39`).
      set text(dir: ltr, size: 0.7em, fill: ink)
      align(center, {
        [#data.form.asset_uid · #data.form.version_label · layout #data.layout_hash]
        if sheet != none [ · #sheet.code]
      })
    }))
}

#let double = opts.sides == "double"
#set page(
  width: data.paper.w * 1mm,
  height: data.paper.h * 1mm,
  margin: if double {
    (top: geo.margin_top * 1mm, bottom: geo.margin_bottom * 1mm,
     inside: (geo.margin_x + geo.binding) * 1mm, outside: geo.margin_x * 1mm)
  } else {
    (top: geo.margin_top * 1mm, bottom: geo.margin_bottom * 1mm, x: geo.margin_x * 1mm)
  },
  foreground: furniture,
)

// -- answer widgets ----------------------------------------------------------------

#let cell(id, si, ci, charset) = marked-box(
  (f: id, k: "cell", s: si, i: ci, cs: charset),
  width: cell-w, height: cell-h, stroke: stroke-w + ink,
)

#let comb-row(q, ri, row) = {
  // If any segment has a caption, all get a caption line, so the boxes stay level.
  let captioned = row.segments.any(seg => seg.caption != none)
  let parts = ()
  if row.label != none { parts.push(box(width: 14mm, height: cell-h, align(horizon, text(size: 0.85em, row.label)))) }
  for (si, seg) in row.segments.enumerate() {
    let seg-index = ri * 100 + si
    let cells = range(seg.cells).map(ci => cell(q.id, seg-index, ci, seg.charset))
    parts.push(stack(dir: ttb, spacing: 0.8mm,
      stack(dir: ltr, spacing: cell-gap, ..cells),
      if captioned { align(center, text(size: 0.65em, fill: ink, if seg.caption != none { seg.caption } else { sym.zws })) },
    ))
    if seg.sep != none { parts.push(box(height: cell-h, inset: (x: 0.8mm), align(bottom, text(size: 1.4em, seg.sep)))) }
    else { parts.push(h(2.5mm)) }
  }
  // Numbers are written left to right on every page, and a ltr stack aligns its items
  // at the top.
  stack(dir: ltr, ..parts)
}

#let choice-item(q, c) = box(inset: (right: 6mm, y: 0.6mm), {
  box(baseline: 20%, marked-box((f: q.id, k: "choice", v: c.value),
    width: bubble, height: bubble, radius: if q.widget.multiple { 0.6mm } else { bubble / 2 },
    stroke: stroke-w + ink))
  h(1.8mm)
  inline(c.label)
})

#let widget(q) = {
  let w = q.widget
  if w.kind == "choices" {
    if w.layout == "horizontal" {
      for c in w.choices { choice-item(q, c) }
    } else if w.layout == "columns" {
      grid(columns: (1fr,) * w.columns, row-gutter: 1.6mm, ..w.choices.map(c => choice-item(q, c)))
    } else {
      stack(dir: ttb, spacing: 1.6mm, ..w.choices.map(c => choice-item(q, c)))
    }
  } else if w.kind == "comb" {
    stack(dir: ttb, spacing: 2mm, ..w.rows.enumerate().map(((ri, row)) => comb-row(q, ri, row)))
  } else if w.kind == "lines" {
    layout(size => {
      let lh = geo.line_h * 1mm
      box(width: size.width, height: w.lines * lh, {
        place(top + left, mark((f: q.id, k: "area", w: size.width.mm(), h: w.lines * geo.line_h)))
        for i in range(w.lines) {
          place(top + left, dy: (i + 1) * lh, line(length: size.width, stroke: 0.5pt + faint))
        }
      })
    })
  } else if w.kind == "box" {
    layout(size => {
      let bh = w.height_mm * 1mm
      box(width: size.width, height: bh, stroke: stroke-w + ink, radius: 1mm, {
        place(top + left, mark((f: q.id, k: "area", w: size.width.mm(), h: w.height_mm)))
        if w.note != none { place(bottom + end, dy: -1.5mm, pad(x: 2mm, text(size: 0.7em, fill: faint, w.note))) }
      })
    })
  } else {
    text(size: 0.85em, fill: ink, style: "italic", w.note)
  }
}

// -- questions and sections ----------------------------------------------------------

#let small(texts) = multi(texts, size: 0.82em, style: "italic")

#let question(q) = block(breakable: false, width: 100%, above: 3.5mm, below: 0mm, grid(
  columns: (if opts.show_numbers { 9mm } else { 0mm }, 1fr),
  if opts.show_numbers { text(weight: "bold", [#q.number.]) },
  {
    let star = if q.required { text(fill: red, weight: "bold", [ \*]) }
    let names = if opts.show_names { [#h(2mm)#text(size: 0.7em, fill: faint, weight: "regular", dir: ltr, q.id)] }
    multi(q.label, weight: "bold", after: [#star#names])
    if q.hint != none and opts.show_hints { block(above: 1mm, multi(q.hint, size: 0.85em, style: "italic", fill: ink)) }
    if q.condition != none and opts.show_conditions { block(above: 1mm, small(q.condition)) }
    block(above: 1.8mm, widget(q))
  },
))

#let note(n) = block(width: 100%, above: 3.5mm, {
  multi(n.label)
  if n.hint != none { block(above: 1mm, multi(n.hint, size: 0.85em, style: "italic", fill: ink)) }
})

#let items(list) = {
  for item in list {
    if item.kind == "question" { question(item) }
    else if item.kind == "note" { note(item) }
    else {
      // A group, or one instance of a repeat.
      block(above: 5mm, below: 0mm, width: 100%, breakable: true, {
        if item.label != none or item.instance_label != none {
          block(width: 100%, fill: rgb("#eeeeee"), inset: (x: 2mm, y: 1.6mm), radius: 0.8mm, sticky: true, {
            grid(columns: (1fr, auto), column-gutter: 3mm,
              if item.label != none { multi(item.label, weight: "bold") },
              if item.instance_label != none { text(size: 0.85em, item.instance_label) },
            )
            if item.condition != none and opts.show_conditions { block(above: 1mm, small(item.condition)) }
          })
        }
        pad(left: if item.kind == "repeat" and data.dir != "rtl" { 2mm } else { 0mm },
            right: if item.kind == "repeat" and data.dir == "rtl" { 2mm } else { 0mm },
            items(item.children))
      })
    }
  }
}

// -- the document ----------------------------------------------------------------------

#let dot = box(baseline: 20%, circle(radius: 1.5mm, fill: black, stroke: none))
#let instructions = block(width: 100%, inset: 2mm, stroke: 0.5pt + faint, radius: 0.8mm, below: 2mm, {
  set text(size: 0.8em, fill: ink)
  stack(dir: ttb, spacing: 1.2mm, ..data.instructions.map(t => block({
    set text(lang: lang-of(t), dir: dir-of(t))
    let parts = t.text.split("{dot}")
    parts.at(0)
    for part in parts.slice(1) { dot; part }
    h(1fr)
    t.required
  })))
})

#let body = if opts.columns > 1 {
  columns(opts.columns, gutter: geo.column_gutter * 1mm, items(model.items))
} else {
  items(model.items)
}

#for (i, sheet) in data.sheets.enumerate() {
  if i > 0 { pagebreak() }
  if opts.show_instructions { instructions }
  body
  if data.pad_blank {
    pagebreak()
    align(center + horizon, text(fill: faint, strings.blank))
  }
}
