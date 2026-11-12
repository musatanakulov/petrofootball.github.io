"""Казахские переводы интерфейса (запускает tests/i18n_check.js на Node, если Node установлен)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js не установлен")
def test_kazakh_translations():
    result = subprocess.run(["node", str(ROOT / "tests" / "i18n_check.js")], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_i18n_script_is_served(client):
    r = client.get("/i18n.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
