// The sheet designer: every change re-renders a preview of one sheet.
(() => {
  const root = document.querySelector(".designer");
  const base = `/api/forms/${root.dataset.asset}/${root.dataset.version}`;
  const panel = document.getElementById("options");
  const pagesEl = document.getElementById("pages");
  const statusEl = document.getElementById("status");
  const warningsEl = document.getElementById("warnings");
  const resultEl = document.getElementById("result");
  const showMarks = document.getElementById("show_marks");
  let timer = null;
  let seq = 0;
  let last = null;

  function options() {
    const val = (name) => panel.querySelector(`[name="${name}"]`);
    const out = {};
    for (const el of panel.querySelectorAll("select[name], input[name]")) {
      if (["primary", "also"].includes(el.name)) continue;
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    }
    const primary = val("primary");
    if (primary) {
      const also = [...panel.querySelectorAll('[name="also"]:checked')]
        .map((el) => el.value)
        .filter((v) => v !== primary.value);
      out.languages = [primary.value, ...also];
    }
    return out;
  }

  function el(tag, attrs = {}, text = "") {
    const e = document.createElement(tag);
    Object.assign(e, attrs);
    if (text) e.textContent = text;
    return e;
  }

  function draw(data) {
    pagesEl.replaceChildren();
    warningsEl.replaceChildren(...data.warnings.map((w) => el("li", {}, w)));
    const parts = [];
    for (const layout of data.layouts) {
      parts.push(`${layout.pages_per_sheet} page(s) a sheet · layout ${layout.layout_hash} · ${layout.languages.join(" + ") || "default language"}`);
      const group = el("div", { className: "layout" });
      layout.pages.forEach((src, i) => {
        const page = el("div", { className: "page" });
        page.style.aspectRatio = `${layout.paper.w} / ${layout.paper.h}`;
        page.append(el("img", { src, alt: `Page ${i + 1}` }));
        if (showMarks.checked) {
          for (const m of layout.marks.filter((m) => m.page === i + 1)) {
            const box = el("div", { className: `mark ${m.k}` });
            box.style.left = `${(m.x / layout.paper.w) * 100}%`;
            box.style.top = `${(m.y / layout.paper.h) * 100}%`;
            box.style.width = `${(m.w / layout.paper.w) * 100}%`;
            box.style.height = `${(m.h / layout.paper.h) * 100}%`;
            page.append(box);
          }
        }
        page.append(el("div", { className: "page-no" }, `${i + 1}`));
        group.append(page);
      });
      pagesEl.append(group);
    }
    statusEl.textContent = parts.join("  |  ");
    statusEl.className = "muted";
  }

  async function refresh() {
    const mine = ++seq;
    statusEl.textContent = "Rendering…";
    const resp = await fetch(`${base}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ options: options() }),
    });
    const data = await resp.json();
    if (mine !== seq) return; // a newer preview is on its way
    if (!resp.ok) {
      statusEl.textContent = data.error || data.detail || "Rendering failed.";
      statusEl.className = "error";
      return;
    }
    last = data;
    draw(data);
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }

  // The page language is always printed: don't offer it again under "Also print in".
  function syncLanguages() {
    const primary = panel.querySelector('[name="primary"]');
    if (!primary) return;
    for (const box of panel.querySelectorAll('[name="also"]')) {
      box.disabled = box.value === primary.value;
      if (box.disabled) box.checked = false;
      box.parentElement.classList.toggle("muted", box.disabled);
    }
  }

  panel.addEventListener("input", (e) => {
    if (e.target.name === "primary") syncLanguages();
    if (e.target.id === "copies") return;
    if (e.target === showMarks) { if (last) draw(last); return; }
    schedule();
  });

  document.getElementById("generate").addEventListener("click", async () => {
    resultEl.textContent = "Generating…";
    const resp = await fetch(`${base}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ options: options(), copies: document.getElementById("copies").value }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      resultEl.textContent = data.error || data.detail || "Failed.";
      resultEl.className = "error";
      return;
    }
    resultEl.className = "";
    resultEl.replaceChildren(
      el("a", { href: data.url, target: "_blank" }, `Download PDF`),
      document.createTextNode(` · ${data.sheets} sheet(s), ${data.pages} page(s), batch ${data.batch}`),
    );
  });

  syncLanguages();
  refresh();
})();
