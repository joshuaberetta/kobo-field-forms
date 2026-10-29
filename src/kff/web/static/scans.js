// Upload scans, then show how each page registered.
(() => {
  const form = document.getElementById("upload");
  const out = document.getElementById("upload-result");
  const el = (tag, attrs = {}, text = "") => {
    const e = document.createElement(tag);
    Object.assign(e, attrs);
    if (text) e.textContent = text;
    return e;
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    out.textContent = "Reading…";
    const resp = await fetch("/api/scans", { method: "POST", body: new FormData(form) });
    const data = await resp.json();
    if (!resp.ok) { out.textContent = data.error || data.detail || "Failed."; return; }
    const list = el("div", { className: "scan-results" });
    for (const r of data.results) {
      const item = el("div", { className: "scan" });
      if (r.error) {
        item.append(el("strong", {}, r.source), el("p", { className: "error" }, r.error));
      } else {
        item.append(
          el("strong", {}, `${r.source}: sheet ${r.sheet}, page ${r.page}/${r.pages}`),
          el("p", { className: "muted" }, `${r.markers}/4 markers · fit ${r.residual_mm} mm`),
          ...r.notes.map((n) => el("p", { className: "note" }, n)),
          el("img", { src: `/scans/${r.scan_id}/overlay.png`, alt: "registered page" }),
        );
      }
      list.append(item);
    }
    const sheets = data.sheets.map((s) =>
      el("li", {}, `Sheet ${s.sheet}: ${s.found.length}/${s.pages} pages${s.missing.length ? ` (missing ${s.missing.join(", ")})` : " (complete)"}`));
    out.replaceChildren(el("ul", {}, ""), list);
    out.firstChild.append(...sheets);
  });
})();
