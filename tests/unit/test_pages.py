import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("build_pages", ROOT / "scripts" / "build_pages.py")
build_pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_pages)


def test_pages_site_is_self_contained(tmp_path):
    index = build_pages.build(tmp_path / "site", "https://github.com/x/y")
    html = index.read_text()
    # A project site lives under /<repo>/: nothing may point at the domain root, and the
    # app's own pages don't exist there.
    assert not re.findall(r'(?:href|src)="/', html)
    assert "View on GitHub" in html and 'href="https://github.com/x/y"' in html
    for ref in re.findall(r'(?:href|src)="(static/[^"]+)"', html):
        assert (tmp_path / "site" / ref).is_file(), ref
    css = (tmp_path / "site" / "static" / "site" / "home.css").read_text()
    for font in re.findall(r'url\("([^"]+)"\)', css):
        assert not font.startswith("/") and (tmp_path / "site" / "static" / "site" / font).is_file()
