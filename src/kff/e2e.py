"""`kff e2e`: the whole loop against a real Kobo server, on synthetic sheets.

For each print profile: render sheets, fill them in with random valid answers, degrade
them like scans, scan, read, submit (readings flagged for review are accepted as they are),
then read the submissions back from Kobo and compare them with what was written. The
project is a throwaway `kff-e2e-*` one, deleted at the end.

Two numbers per question type:
- **read accuracy:** the reading matches what was written (the recognisers);
- **in Kobo:** what Kobo stored matches what was sent (the submission path). This must be
  exactly 100%.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kff.bench import BenchResult, run, same
from kff.config import Settings
from kff.errors import KffError
from kff.fetch import fetch_form
from kff.kobo.client import KoboClient
from kff.kobo.deploy import TEST_PREFIX, create_and_deploy, delete_test_project
from kff.read.vlm import VlmConfig, VlmReader
from kff.render.options import RenderOptions
from kff.render.pdf import Sheet
from kff.store import FormVersion, Store
from kff.submit import assemble, submission_values, submit_sheet

FORM = Path(__file__).with_name("e2e_form.json")
PROFILES = {
    "a4-en": RenderOptions(),
    "letter-double-en-ar": RenderOptions(paper="us-letter", sides="double", languages=("en", "ar")),
    "a4-landscape-columns": RenderOptions(
        orientation="landscape", languages=("en", "ar"), language_layout="columns", columns=2
    ),
    "a5-ar-large": RenderOptions(paper="a5", languages=("ar",), font_size=12),
}
# The e2e run fails below these (read accuracy, by question type).
THRESHOLDS = {"select_one": 0.98, "select_multiple": 0.95, "acknowledge": 0.98, "integer": 0.8}


@dataclass
class E2EReport:
    server: str | None
    project: str | None
    profiles: dict[str, Any] = field(default_factory=dict)
    kobo_mismatches: list[dict[str, Any]] = field(default_factory=list)
    duplicate_check: str | None = None
    failures: list[str] = field(default_factory=list)
    seconds: float = 0

    def to_json(self) -> dict[str, Any]:
        return self.__dict__


def _offline_form(content: dict[str, Any]) -> FormVersion:
    return FormVersion(
        "offline",
        "aE2eOffline0000000000",
        "vE2eOffline0000000000",
        "kff e2e (offline)",
        "1",
        "2026-01-01T00:00:00Z",
        "0" * 32,
        None,
        content,
    )


def run_e2e(
    settings: Settings,
    *,
    profiles: list[str],
    sheets: int,
    seed: int,
    offline: bool,
    keep_project: bool,
    log=print,
) -> E2EReport:
    started = time.monotonic()
    content = json.loads(FORM.read_text())
    home = settings.home / f"e2e-{time.strftime('%Y%m%d-%H%M%S')}"
    config = VlmConfig.from_env()
    vlm = VlmReader(config) if config else None
    log(f"store: {home}; text reader: {config.url if config else 'none'}")
    client: KoboClient | None = None
    uid: str | None = None
    report = E2EReport(None if offline else settings.server, None)
    try:
        with Store(home) as store:
            if offline:
                form = _offline_form(content)
            else:
                client = KoboClient(settings)
                uid = create_and_deploy(
                    client, f"{TEST_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}", content
                )
                report.project = uid
                log(f"created and deployed {uid}")
                form = fetch_form(client, settings.server, uid)
            sent: dict[str, dict[str, str]] = {}
            types: dict[str, str] = {}
            for i, name in enumerate(profiles):
                log(f"[{name}] {sheets} sheet(s)…")
                result, written = run(
                    store, form, PROFILES[name], sheets=sheets, seed=seed + i, vlm=vlm, profile=name
                )
                summary = result.summary()
                report.profiles[name] = summary
                report.failures += [f"{name}: scan failed: {f}" for f in result.scan_failures]
                _check_thresholds(name, result, report)
                log(_line(summary))
                for sheet_id, _ in written:
                    layout = store.get_layout(store.scans(sheet_id)[0]["layout_hash"])
                    types.update({k: v["type"] for k, v in layout["fields"].items()})
                    if client is None:
                        continue
                    done = submit_sheet(store, client, sheet_id, accept_flagged=True)
                    if done.status != 201:
                        report.failures.append(f"{name}: submission returned {done.status}")
                    a = assemble(form, layout, store.readings(sheet_id), accept_flagged=True)
                    sent[Sheet(sheet_id, 0).instance_id] = a.answers
            if client is not None and uid:
                _compare_kobo(client, uid, sent, types, report)
                first = next(iter(sent), None)
                if first:
                    sheet_id = first.removeprefix("uuid:").replace("-", "")
                    again = submit_sheet(store, client, sheet_id, accept_flagged=True)
                    report.duplicate_check = (
                        "202 duplicate" if again.status == 202 else str(again.status)
                    )
                    if again.status != 202:
                        report.failures.append(f"resubmitting a sheet gave {again.status}, not 202")
    finally:
        if vlm:
            vlm.close()
        if client is not None:
            if uid and not keep_project:
                delete_test_project(client, uid)
                log(f"deleted {uid}")
            client.close()
    report.seconds = round(time.monotonic() - started, 1)
    return report


def _line(summary: dict[str, Any]) -> str:
    parts = [f"{t} {s['accuracy']:.0%}" for t, s in summary["by_type"].items()]
    a = summary["all"]
    return (
        f"  read {a['accuracy']:.1%} of {a['fields']} fields; flagged {a['flagged_rate']:.0%}; "
        f"wrong and unflagged {a['wrong_unflagged_rate']:.1%}  ({', '.join(parts)})"
    )


def _check_thresholds(name: str, result: BenchResult, report: E2EReport) -> None:
    for qtype, stats in result.summary()["by_type"].items():
        floor = THRESHOLDS.get(qtype)
        if floor is not None and stats["accuracy"] < floor:
            report.failures.append(f"{name}: {qtype} read {stats['accuracy']:.1%} < {floor:.0%}")


def _compare_kobo(
    client: KoboClient,
    uid: str,
    sent: dict[str, dict[str, str]],
    types: dict[str, str],
    report: E2EReport,
) -> None:
    stored: dict[str, dict[str, str]] = {}
    for record in client.paginate(f"/api/v2/assets/{uid}/data/", page_size=500):
        stored[record.get("meta/instanceID")] = submission_values(record)
    for instance, answers in sent.items():
        got = stored.get(instance)
        if got is None:
            report.kobo_mismatches.append({"instance": instance, "missing": True})
            continue
        for fid, value in answers.items():
            qtype = types.get(fid, "text")
            if qtype == "image":
                continue
            if not same(qtype, value, got.get(fid)):
                report.kobo_mismatches.append(
                    {"instance": instance, "field": fid, "sent": value, "stored": got.get(fid)}
                )
    if report.kobo_mismatches:
        report.failures.append(f"{len(report.kobo_mismatches)} value(s) differ in Kobo")


def main_e2e(args) -> None:
    settings = Settings.from_env(need_token=not args.offline)
    names = list(PROFILES) if args.profiles == "all" else args.profiles.split(",")
    unknown = [n for n in names if n not in PROFILES]
    if unknown:
        raise KffError(f"Unknown profile(s) {unknown}; choose from {', '.join(PROFILES)}.")
    report = run_e2e(
        settings,
        profiles=names,
        sheets=args.sheets,
        seed=args.seed,
        offline=args.offline,
        keep_project=args.keep_project,
    )
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_json(), indent=1, ensure_ascii=False))
    print(f"report: {out}  ({report.seconds} s)")
    if report.duplicate_check:
        print(f"resubmitted a sheet: {report.duplicate_check}")
    if report.failures:
        for f in report.failures:
            print(f"FAIL: {f}")
        raise KffError(f"e2e failed: {len(report.failures)} problem(s).")
    print("e2e passed.")
