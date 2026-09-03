"""Phase 1 驗收:限速、快取、重試、編碼。

這些測試完全不連網 —— 用假的 Session 取代 requests.Session。
"""

from __future__ import annotations

import time

import pytest
import requests
from tenacity import wait_none

from crawler.config import MIN_DELAY
from crawler.http import ClientError, Fetcher, ServerError, resolve_delay


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.content = body
        self.status_code = status


class FakeSession:
    """記錄呼叫次數的假 Session。"""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


@pytest.fixture
def no_sleep(monkeypatch):
    """攔截 sleep,記錄被要求睡多久,但實際不睡。"""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


# -- 限速下限 ---------------------------------------------------------------


def test_delay_defaults_to_one_second(monkeypatch):
    monkeypatch.delenv("CRAWL_DELAY", raising=False)
    assert resolve_delay() == 1.0


def test_delay_floor_cannot_be_bypassed_by_argument():
    """plan.md §1.4:下限 0.5 秒,不得為了加速而放寬。"""
    assert resolve_delay(0) == MIN_DELAY
    assert resolve_delay(-5) == MIN_DELAY
    assert resolve_delay(0.1) == MIN_DELAY


def test_delay_floor_cannot_be_bypassed_by_env(monkeypatch):
    monkeypatch.setenv("CRAWL_DELAY", "0")
    assert resolve_delay() == MIN_DELAY


def test_delay_above_floor_is_respected(monkeypatch):
    monkeypatch.delenv("CRAWL_DELAY", raising=False)
    assert resolve_delay(2.5) == 2.5
    monkeypatch.setenv("CRAWL_DELAY", "3")
    assert resolve_delay() == 3.0


def test_invalid_env_delay_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CRAWL_DELAY", "fast")
    assert resolve_delay() == 1.0


def test_fetcher_sleeps_after_each_real_request(tmp_path, no_sleep):
    session = FakeSession(FakeResponse(b"<html>ok</html>"))
    f = Fetcher(delay=0.7, cache_dir=tmp_path, session=session)
    f.fetch("Subj.jsp", params={"format": -2})
    assert no_sleep == [0.7]


# -- 編碼 -------------------------------------------------------------------


def test_decodes_utf8_chinese(tmp_path, no_sleep):
    body = "<html>資工系 電資學院</html>".encode("utf-8")
    f = Fetcher(cache_dir=tmp_path, session=FakeSession(FakeResponse(body)))
    assert "資工系" in f.fetch("Subj.jsp")


# -- 快取 -------------------------------------------------------------------


def test_cache_hit_makes_no_request_and_no_sleep(tmp_path, no_sleep):
    session = FakeSession(FakeResponse("<html>資工四</html>".encode("utf-8")))
    f = Fetcher(cache_dir=tmp_path, session=session)

    first = f.fetch("Subj.jsp", params={"format": -4, "code": "2915"})
    second = f.fetch("Subj.jsp", params={"format": -4, "code": "2915"})

    assert first == second == "<html>資工四</html>"
    assert len(session.calls) == 1, "第二次應該完全不發請求"
    assert no_sleep == [f.delay], "命中快取不應該再 sleep"
    assert f.request_count == 1
    assert f.cache_hit_count == 1


def test_cache_survives_a_new_fetcher(tmp_path, no_sleep):
    """開發期反覆執行 CLI 時要能沿用上一輪的快取。"""
    session_a = FakeSession(FakeResponse(b"<html>a</html>"))
    Fetcher(cache_dir=tmp_path, session=session_a).fetch("Subj.jsp")

    session_b = FakeSession(FakeResponse(b"<html>SHOULD NOT BE USED</html>"))
    html = Fetcher(cache_dir=tmp_path, session=session_b).fetch("Subj.jsp")

    assert html == "<html>a</html>"
    assert session_b.calls == []


def test_no_cache_always_refetches(tmp_path, no_sleep):
    session = FakeSession(
        FakeResponse(b"<html>1</html>"), FakeResponse(b"<html>2</html>")
    )
    f = Fetcher(cache_dir=tmp_path, use_cache=False, session=session)
    assert f.fetch("Subj.jsp") == "<html>1</html>"
    assert f.fetch("Subj.jsp") == "<html>2</html>"
    assert len(session.calls) == 2


def test_different_params_are_different_cache_entries(tmp_path, no_sleep):
    session = FakeSession(
        FakeResponse(b"<html>dept</html>"), FakeResponse(b"<html>class</html>")
    )
    f = Fetcher(cache_dir=tmp_path, session=session)
    assert f.fetch("Subj.jsp", params={"code": "59"}) == "<html>dept</html>"
    assert f.fetch("Subj.jsp", params={"code": "31"}) == "<html>class</html>"


# -- 重試 -------------------------------------------------------------------


def test_4xx_raises_immediately_without_retry(tmp_path, no_sleep):
    """被擋時重試只會擋得更死,所以 4xx 不重試。"""
    session = FakeSession(FakeResponse(b"", status=403))
    f = Fetcher(cache_dir=tmp_path, session=session)

    with pytest.raises(ClientError):
        f.fetch("Subj.jsp")
    assert len(session.calls) == 1


def test_5xx_retries_up_to_four_attempts(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())
    session = FakeSession(FakeResponse(b"", status=503))
    f = Fetcher(cache_dir=tmp_path, session=session)

    with pytest.raises(ServerError):
        f.fetch("Subj.jsp")
    assert len(session.calls) == 4


def test_5xx_then_success_returns_the_body(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())
    session = FakeSession(
        FakeResponse(b"", status=500), FakeResponse(b"<html>ok</html>")
    )
    f = Fetcher(cache_dir=tmp_path, session=session)
    assert f.fetch("Subj.jsp") == "<html>ok</html>"
    assert len(session.calls) == 2


def test_connection_error_is_retried(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())

    calls = []

    class FlakySession(FakeSession):
        def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) < 3:
                raise requests.ConnectionError("boom")
            return FakeResponse(b"<html>ok</html>")

    f = Fetcher(cache_dir=tmp_path, session=FlakySession())
    assert f.fetch("Subj.jsp") == "<html>ok</html>"
    assert len(calls) == 3


# -- URL 組裝 ---------------------------------------------------------------


def test_build_url_joins_base_and_params(tmp_path):
    f = Fetcher(cache_dir=tmp_path, session=FakeSession())
    url = f.build_url("Subj.jsp", {"format": -2, "year": 115, "sem": 1})
    assert url == "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-2&year=115&sem=1"
