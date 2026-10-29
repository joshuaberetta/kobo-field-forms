"""Build the GitHub Pages site: the app's homepage as a static page, in `site/`.

  python scripts/build_pages.py [--out site] [--repo https://github.com/…]

Only needs Jinja2. The page is the app's own template (src/kff/web/templates/home.html)
rendered with `pages=True`: assets under a relative `static/`, and the app's pages named
instead of linked, since there's no app behind GitHub Pages.
"""

import argparse
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "kff" / "web"
REPO = "https://github.com/joshuaberetta/kobo-field-forms"


def build(out: Path, repo: str) -> Path:
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape())
    html = env.get_template("home.html").render(pages=True, static="static", home="./", repo=repo)
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    (out / "index.html").write_text(html)
    for part in ("site", "home"):
        shutil.copytree(WEB / "static" / part, out / "static" / part)
    (out / ".nojekyll").write_text("")  # serve files as they are
    return out / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()
    print(f"wrote {build(args.out, args.repo)}")


if __name__ == "__main__":
    main()
