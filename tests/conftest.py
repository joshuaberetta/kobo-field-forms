import pytest

from kff.config import Settings

TOKEN = "test-token-0123456789abcdef"
BASE = "https://kf.kobotoolbox.org"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_url=BASE, token=TOKEN, home=tmp_path / "data", timeout=5)
