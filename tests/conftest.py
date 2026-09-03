"""共用測試工具。"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """讀取 Phase 0 存下的真實 HTML 樣本。

    刻意用與 http.py 相同的方式解碼(UTF-8),確保測試看到的字串
    和線上抓下來的一模一樣。
    """
    return (FIXTURE_DIR / name).read_bytes().decode("utf-8")


@pytest.fixture
def fixture():
    return load_fixture
