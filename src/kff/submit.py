"""Turn a sheet's readings into one Kobo submission and send it.

Assembly applies the form's rules: answers to questions skip logic hides are dropped,
required answers that are missing and failed constraints are problems, and repeat
entries left empty are left out. The XML follows kobo-mcp's `formcontent/openrosa
.build_xml` (a Collect/Enketo instance), with the sheet id as `meta/instanceID`, so a
sheet submitted twice is Kobo's 202 duplicate, not a second record.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import cv2

from kff.errors import KffError
from kff.form.model import _Node, _tree
from kff.form.rules import rules
from kff.kobo.client import KoboClient
from kff.render.pdf import Sheet
from kff.scan.extract import crop_image
from kff.store import FormVersion, Store

_INSTANCE = re.compile(r"\[(\d+)\]")


@dataclass
class Assembly:
    answers: dict[str, str]  # field id → value
    attachments: dict[str, str] = field(default_factory=dict)  # field id → file name
    problems: list[tuple[str, str]] = field(default_factory=list)  # (field id, why)
    dropped: list[str] = field(default_factory=list)  # answered but hidden by skip logic

    @property
    def ok(self) -> bool:
        return not self.problems


def assemble(
    form: FormVersion,
    layout: dict[str, Any],
    readings: dict[str, dict[str, Any]],
    *,
    accept_flagged: bool = False,
) -> Assembly:
    """`accept_flagged` takes flagged readings as they are (the e2e test's auto mode)."""
    form_rules = rules(form.content)
    answers = {fid: r["value"] for fid, r in readings.items() if r["value"] is not None}
    out = Assembly({})
    # Hidden by skip logic: drop, repeatedly (hiding one answer can hide another).
    while True:
        hidden = [fid for fid in answers if not form_rules.relevant(fid, answers)]
        if not hidden:
            break
        for fid in hidden:
            del answers[fid]
            out.dropped.append(fid)
    for fid, info in layout["fields"].items():
        r = readings.get(fid)
        shown = form_rules.relevant(fid, answers)
        if r and r["needs_review"] and not r["reviewed"] and not accept_flagged and shown:
            out.problems.append((fid, r["reason"] or "low confidence: check the reading"))
        value = answers.get(fid)
        if value is None:
            if info["required"] and shown and not _empty_entry(fid, answers):
                out.problems.append((fid, "required, but no answer was read"))
            continue
        if form_rules.constraint_ok(fid, value, answers) is False:
            out.problems.append((fid, f"{value!r} breaks the question's constraint"))
        if info["type"] == "image":
            out.attachments[fid] = value
    out.answers = answers
    return out


def _empty_entry(fid: str, answers: dict[str, str]) -> bool:
    """A field in a repeat entry nobody filled in: not missing, just an unused row."""
    m = list(_INSTANCE.finditer(fid))
    if not m:
        return False
    prefix = fid[: m[-1].end()]
    return not any(k.startswith(prefix + "/") for k in answers)


def version_label(form: FormVersion) -> str | None:
    """Collect's form version label: `<deployed count> (<date deployed>)`."""
    if not form.date_deployed:
        return None
    return f"{form.version_number} ({form.date_deployed[:19].replace('T', ' ')})"


def build_xml(form: FormVersion, answers: dict[str, str], sheet_id: str) -> bytes:
    attrs = {"id": form.asset_uid}
    if label := version_label(form):
        attrs["version"] = label
    root = ET.Element(form.asset_uid, attrs)
    ET.SubElement(ET.SubElement(root, "formhub"), "uuid").text = form.deployment_uuid

    def fill(elem: ET.Element, nodes: list[_Node], prefix: str) -> None:
        for node in nodes:
            fid = f"{prefix}/{node.name}" if prefix else node.name
            if node.type == "group":
                sub = ET.Element(node.name)
                fill(sub, node.children, fid)
                if len(sub):
                    elem.append(sub)
            elif node.type == "repeat":
                indexes = sorted(
                    {
                        int(k[len(fid) + 1 :].split("]")[0])
                        for k in answers
                        if k.startswith(fid + "[")
                    }
                )
                for i in indexes:
                    sub = ET.SubElement(elem, node.name)
                    fill(sub, node.children, f"{fid}[{i}]")
            elif fid in answers:
                ET.SubElement(elem, node.name).text = answers[fid]

    fill(root, _tree(form.content), "")
    ET.SubElement(root, "__version__").text = form.version_uid
    ET.SubElement(ET.SubElement(root, "meta"), "instanceID").text = Sheet(sheet_id, 0).instance_id
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def attachment_files(
    store: Store, sheet_id: str, layout: dict[str, Any], assembly: Assembly
) -> dict[str, tuple[bytes, str]]:
    """Each image answer as a PNG cut from its scanned page."""
    files: dict[str, tuple[bytes, str]] = {}
    if not assembly.attachments:
        return files
    scans = {s["page"]: s for s in store.scans(sheet_id)}
    for fid, name in assembly.attachments.items():
        mark = next((m for m in layout["marks"] if m["f"] == fid), None)
        scan = scans.get(mark["page"]) if mark else None
        if scan is None:
            continue
        image = cv2.imread(str(store.path.parent / scan["image"]), cv2.IMREAD_GRAYSCALE)
        ok, png = cv2.imencode(".png", crop_image(image, mark, inset=0.0))
        if ok:
            files[name] = (png.tobytes(), "image/png")
    return files


@dataclass
class Submitted:
    sheet_id: str
    status: int  # 201 created, 202 already there
    instance_id: str
    dropped: list[str]


def submit_sheet(
    store: Store, client: KoboClient, sheet_id: str, *, accept_flagged: bool = False
) -> Submitted:
    scans = store.scans(sheet_id)
    if not scans:
        raise KffError("No scans of this sheet.")
    layout = store.get_layout(scans[0]["layout_hash"])
    if layout is None:
        raise KffError("The sheet's layout isn't in the store.")
    missing = set(range(1, layout["pages_per_sheet"] + 1)) - {s["page"] for s in scans}
    missing -= set(layout.get("blank_pages") or [])
    if missing:
        raise KffError(f"Pages {sorted(missing)} of this sheet haven't been scanned.")
    f = layout["form"]
    form = store.get_form(f["server"], f["asset_uid"], f["version_uid"])
    if form is None:
        raise KffError("The sheet's form version isn't in the store.")
    readings = store.readings(sheet_id)
    if not readings:
        raise KffError("The sheet hasn't been read yet (kff read).")
    assembly = assemble(form, layout, readings, accept_flagged=accept_flagged)
    # `accept_flagged` (testing, e2e) submits the readings as they are, problems and all;
    # Kobo's OpenRosa endpoint doesn't enforce required answers or constraints.
    if not assembly.ok and not accept_flagged:
        listed = "; ".join(f"{fid}: {why}" for fid, why in assembly.problems[:5])
        raise KffError(f"{len(assembly.problems)} answer(s) need review first: {listed}")
    xml = build_xml(form, assembly.answers, sheet_id)
    files = attachment_files(store, sheet_id, layout, assembly)
    instance = Sheet(sheet_id, 0).instance_id
    resp = client.submit(xml, files)
    store.record_submission(sheet_id, instance, resp.status_code, resp.text[:500])
    return Submitted(sheet_id, resp.status_code, instance, assembly.dropped)


def submission_values(record: dict[str, Any]) -> dict[str, str]:
    """A submission from `/data/` back into field ids: repeats come as lists of objects
    keyed by full xpath (KB §5.4) and become `members[1]/…`."""
    out: dict[str, str] = {}

    def walk(obj: dict[str, Any], prefix_map: list[tuple[str, str]]) -> None:
        for key, value in obj.items():
            if key.startswith("_") or key.startswith("meta/") or key in ("formhub/uuid",):
                continue
            fid = key
            for plain, indexed in prefix_map:
                if fid.startswith(plain + "/"):
                    fid = indexed + fid[len(plain) :]
            if isinstance(value, list):
                for i, item in enumerate(value, 1):
                    if isinstance(item, dict):
                        walk(item, [(key, f"{fid}[{i}]"), *prefix_map])
            elif value is not None:
                out[fid] = str(value)

    walk(record, [])
    return out
