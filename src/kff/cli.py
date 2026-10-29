"""`kff`: the command line."""

import argparse
import json
import logging
import sys

from kff.config import Settings
from kff.errors import KffError
from kff.fetch import fetch_form, parse_asset_uid
from kff.kobo.client import KoboClient
from kff.store import FormVersion, Store


def _summary(form: FormVersion) -> str:
    langs = ", ".join(lang or "(default)" for lang in form.languages)
    rows = len(form.content.get("survey") or [])
    return (
        f"{form.asset_uid}  {form.version_uid}  v{form.version_number or '?'}  "
        f"{(form.date_deployed or '')[:10]}  {rows} rows  [{langs}]  {form.name}"
    )


def cmd_fetch(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    uid = parse_asset_uid(args.project)
    with KoboClient(settings) as client, Store(settings.home) as store:
        form = store.save_form(fetch_form(client, settings.server, uid, args.version))
    print(_summary(form))


def cmd_forms(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    with Store(settings.home) as store:
        forms = list(store.list_forms(None if args.all_servers else settings.server))
    if args.json:
        print(json.dumps([{**vars(f), "content": None} for f in forms], indent=2))
        return
    for form in forms:
        print(_summary(form))


def cmd_show(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    uid = parse_asset_uid(args.project)
    with Store(settings.home) as store:
        form = store.get_form(settings.server, uid, args.version)
    if form is None:
        raise KffError(f"{uid} isn't in the store for {settings.server}; run `kff fetch` first.")
    print(json.dumps(form.content, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kff", description="Paper forms for KoboToolbox.")
    parser.add_argument("-v", "--verbose", action="store_true", help="log requests")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="fetch a deployed form version into the local store")
    p.add_argument("project", help="project uid or link")
    p.add_argument("--version", help="deployed version uid (default: the current one)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("forms", help="list the form versions in the local store")
    p.add_argument("--all-servers", action="store_true", help="not just KOBO_BASE_URL's")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_forms)

    p = sub.add_parser("show", help="print a stored form version's content as JSON")
    p.add_argument("project", help="project uid or link")
    p.add_argument("--version", help="version uid (default: the newest stored)")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        args.func(args)
    except KffError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
