"""共用測試工具。"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

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


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """安全網:任何測試都不得連到外部網站。

    plan.md §5.3 要求開發期一律用 fixture 或快取,不要反覆打學校伺服器。
    靠自律不夠 —— 只要有人在測試裡不小心建了真的 Fetcher,每跑一次
    pytest 就會對學校發一輪請求。這裡直接讓它炸掉,問題會當場現形。
    """

    def blocked(*args, **kwargs):
        raise AssertionError(
            "測試不可以發出真實 HTTP 請求。請改用 fixture 或假的 Fetcher。"
        )

    monkeypatch.setattr(requests.Session, "request", blocked)
