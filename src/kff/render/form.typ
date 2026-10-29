// A printable Kobo form. All data arrives as JSON in sys.inputs.data (see render/typst.py):
// strings are inserted as text, never evaluated as markup.
//
// Every answer area emits `#metadata(...)<mark>` at its top-left corner, and every page its
// corner markers and QR as `<page-mark>`, so `typst query` gives the scanner an exact layout
// map in mm. Positions are relative to the page's top-left corner.

#let data = json(bytes(sys.inputs.data))
#let opts = data.options
#let model = data.model
#let geo = data.geometry
// Pages per sheet: 0 in the first pass, which renders one sheet to measure it.
#let per-sheet = data.pages_per_sheet

#let ink = rgb("#555555")
#let faint = rgb("#999999")
#let stroke-w = 0.6pt
#let cell-w = geo.cell_w * 1mm
#let cell-h = geo.cell_h * 1mm
#let cell-gap = geo.cell_gap * 1mm
#let bubble = geo.bubble * 1mm

#set document(title: model.title)
// Fonts are tried in order per glyph; `fallback: false` keeps out system fonts, so a
// missing glyph shows as a box instead of silently changing font.
#set text(font: ("Noto Sans", "Noto Naskh Arabic"), size: opts.font_size * 1pt, fallback: false)
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

// -- page furniture: corner markers, QR code, header, footer -----------------------

#let sheet-of(abs) = if per-sheet > 0 { calc.quo(abs - 1, per-sheet) } else { 0 }
#let page-in-sheet(abs) = if per-sheet > 0 { calc.rem(abs - 1, per-sheet) + 1 } else { abs }

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
  let w = page.width.mm()
  let h = page.height.mm()
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
  // The QR code sits left of the top-right marker.
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

  // Title block, between the top-left marker and the QR code.
  let tx = off + size + geo.qr_gap
  place(top + left, dx: tx * 1mm, dy: off * 1mm, block(width: (qx - tx - geo.qr_gap) * 1mm, {
    text(size: 1.25em, weight: "bold", model.title)
    linebreak()
    text(size: 0.8em, fill: ink, {
      if sheet != none [Sheet *#sheet.code*] else [Sheet ----]
      [ · page #p of #calc.max(per-sheet, 1)]
    })
  }))

  // Footer, between the bottom markers.
  place(top + left, dx: tx * 1mm, dy: (h - off - size / 2 - 2) * 1mm,
    block(width: (w - 2 * tx) * 1mm, align(center, text(size: 0.7em, fill: ink, {
      [#data.form.asset_uid · version #data.form.version_label · layout #data.layout_hash]
      if sheet != none [ · sheet #sheet.code]
    }))))
}

#set page(
  paper: opts.paper,
  margin: (top: geo.margin_top * 1mm, bottom: geo.margin_bottom * 1mm, x: geo.margin_x * 1mm),
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
  if row.label != none { parts.push(box(width: 12mm, height: cell-h, align(horizon, text(size: 0.85em, row.label)))) }
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
  // A left-to-right stack aligns its items at the top.
  stack(dir: ltr, ..parts)
}

#let choice-item(q, c) = box(inset: (right: 6mm, y: 0.6mm), {
  box(baseline: 20%, marked-box((f: q.id, k: "choice", v: c.value),
    width: bubble, height: bubble, radius: if q.widget.multiple { 0.6mm } else { bubble / 2 },
    stroke: stroke-w + ink))
  h(1.8mm)
  c.label
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
        if w.note != none { place(bottom + right, dx: -2mm, dy: -1.5mm, text(size: 0.7em, fill: faint, w.note)) }
      })
    })
  } else {
    text(size: 0.85em, fill: ink, style: "italic", w.note)
  }
}

// -- questions and sections ----------------------------------------------------------

#let condition-line(c, what) = if c != none and opts.show_conditions {
  text(size: 0.82em, fill: ink, style: "italic")[Answer #what only if #c.]
}

#let question(q) = block(breakable: false, width: 100%, above: 3.5mm, below: 0mm, grid(
  columns: (if opts.show_numbers { 9mm } else { 0mm }, 1fr),
  if opts.show_numbers { text(weight: "bold", [#q.number.]) },
  {
    text(weight: "bold", q.label)
    if q.required { text(fill: rgb("#b00020"), weight: "bold", [ \*]) }
    if opts.show_names { h(2mm); text(size: 0.7em, fill: faint, font: "Noto Sans", q.id) }
    if q.hint != none and opts.show_hints { linebreak(); text(size: 0.85em, fill: ink, style: "italic", q.hint) }
    if q.condition != none and opts.show_conditions { linebreak(); condition-line(q.condition, "this") }
    v(1.6mm, weak: true)
    block(above: 1.6mm, widget(q))
  },
))

#let note(n) = block(width: 100%, above: 3.5mm, {
  text(n.label)
  if n.hint != none { linebreak(); text(size: 0.85em, fill: ink, style: "italic", n.hint) }
})

#let items(list) = {
  for item in list {
    if item.kind == "question" { question(item) }
    else if item.kind == "note" { note(item) }
    else {
      // A group, or one instance of a repeat.
      let title = item.label
      if item.kind == "repeat" {
        title = [#if title != none { title } else [Entry] #h(1fr) #text(weight: "regular", size: 0.85em)[#item.instance of #item.instances]]
      }
      block(above: 5mm, below: 0mm, width: 100%, breakable: true, {
        if title != none {
          block(width: 100%, fill: rgb("#eeeeee"), inset: (x: 2mm, y: 1.6mm), radius: 0.8mm, sticky: true, {
            text(weight: "bold", title)
            if item.condition != none and opts.show_conditions {
              linebreak()
              condition-line(item.condition, "this section")
            }
          })
        }
        pad(left: if item.kind == "repeat" { 2mm } else { 0mm }, items(item.children))
      })
    }
  }
}

// -- the document ----------------------------------------------------------------------

#let instructions = block(width: 100%, inset: 2mm, stroke: 0.5pt + faint, radius: 0.8mm, text(size: 0.8em, fill: ink)[
  Use a dark pen. Fill bubbles completely:
  #box(baseline: 20%, circle(radius: 1.5mm, fill: black, stroke: none)) ·
  one letter or digit per box · keep writing inside the boxes. #h(1fr) \* required
])

#for (i, sheet) in data.sheets.enumerate() {
  if i > 0 { pagebreak() }
  instructions
  items(model.items)
}
