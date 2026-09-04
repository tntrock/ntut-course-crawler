"""Phase 1 驗收:限速、快取、重試、編碼。

這些測試完全不連網 —— 用假的 Session 取代 requests.Session。
"""

from __future__ import annotations

import time

import pytest
import requests
from tenacity import wait_none

from crawler.config import (
    MIN_DELAY,
    RETRY_ATTEMPTS_PER_ROUND,
    RETRY_ROUND_PAUSE,
    RETRY_ROUNDS,
    UNAVAILABLE_AFTER,
)
from crawler.http import (
    ClientError,
    Fetcher,
    ServerError,
    SiteUnavailable,
    resolve_delay,
)

MAX_ATTEMPTS = RETRY_ATTEMPTS_PER_ROUND * RETRY_ROUNDS


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


def test_5xx_retries_every_attempt_in_every_round(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())
    session = FakeSession(FakeResponse(b"", status=503))
    f = Fetcher(cache_dir=tmp_path, session=session)

    with pytest.raises(ServerError):
        f.fetch("Subj.jsp")
    assert len(session.calls) == MAX_ATTEMPTS


def test_rounds_are_separated_by_a_long_pause(tmp_path, no_sleep):
    """輪內是幾秒的指數退避,一輪打完全滅才等 3 分鐘。

    2026-09-04 那次排程就是連四次 connect 逾時、55 秒就整個放棄。跨太平洋
    的線路抖動常常一分鐘內就恢復 —— 退一步等久一點,比整批重跑便宜得多。
    """
    session = FakeSession(FakeResponse(b"", status=503))
    f = Fetcher(cache_dir=tmp_path, session=session)

    with pytest.raises(ServerError):
        f.fetch("Subj.jsp")

    # 最後一次失敗後不會再等,所以 wait 只有 MAX_ATTEMPTS - 1 次
    waits = no_sleep[: MAX_ATTEMPTS - 1]
    assert len(waits) == MAX_ATTEMPTS - 1
    assert waits[: RETRY_ATTEMPTS_PER_ROUND - 1] == [2, 4, 8], "輪內應該是指數退避"
    assert waits[RETRY_ATTEMPTS_PER_ROUND - 1] == RETRY_ROUND_PAUSE, "輪與輪之間要長休息"
    assert RETRY_ROUND_PAUSE not in waits[: RETRY_ATTEMPTS_PER_ROUND - 1]


# -- 斷路器 -----------------------------------------------------------------


class DeadSession(FakeSession):
    """怎麼打都連不上,模擬學校端整個不可用。"""

    def get(self, url, **kwargs):
        self.calls.append(url)
        raise requests.ConnectTimeout("connect timed out")


def _dead_fetcher(tmp_path, monkeypatch):
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())
    return Fetcher(cache_dir=tmp_path, use_cache=False, session=DeadSession())


def test_circuit_opens_after_enough_consecutive_dead_urls(tmp_path, no_sleep, monkeypatch):
    f = _dead_fetcher(tmp_path, monkeypatch)

    for n in range(UNAVAILABLE_AFTER):
        assert not f.unavailable, f"第 {n + 1} 個網址之前不該提早判定"
        with pytest.raises(requests.ConnectionError):
            f.fetch("Subj.jsp", params={"code": str(n)})

    assert f.unavailable
    assert f.consecutive_failures == UNAVAILABLE_AFTER


def test_open_circuit_fails_fast_without_touching_the_network(
    tmp_path, no_sleep, monkeypatch
):
    """判定不可用之後就別再打了。

    沒有這道閘,一次全站中斷會讓幾百個網址各自耗掉整套重試(每個好幾分鐘),
    對學校是噪音,對我們是把 runner 燒到 job 逾時。
    """
    f = _dead_fetcher(tmp_path, monkeypatch)
    for n in range(UNAVAILABLE_AFTER):
        with pytest.raises(requests.ConnectionError):
            f.fetch("Subj.jsp", params={"code": str(n)})

    before = len(f.session.calls)
    with pytest.raises(SiteUnavailable):
        f.fetch("Subj.jsp", params={"code": "999"})
    assert len(f.session.calls) == before, "斷路器跳開後不該再發任何請求"


def test_a_success_resets_the_failure_streak(tmp_path, no_sleep, monkeypatch):
    """偶發的單頁失敗不該累積成「整站不可用」。"""
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())

    state = {"fail": True}

    class FlakySession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if state["fail"]:
                raise requests.ConnectTimeout("connect timed out")
            return FakeResponse(b"<html>ok</html>")

    f = Fetcher(cache_dir=tmp_path, use_cache=False, session=FlakySession())

    with pytest.raises(requests.ConnectionError):
        f.fetch("Subj.jsp", params={"code": "1"})
    assert f.consecutive_failures == 1

    state["fail"] = False
    f.fetch("Subj.jsp", params={"code": "2"})
    assert f.consecutive_failures == 0
    assert not f.unavailable


def test_4xx_does_not_count_towards_the_circuit(tmp_path, no_sleep):
    """4xx 代表對方其實是活的,只是這個網址有問題,不該拿來判定整站不可用。"""
    f = Fetcher(cache_dir=tmp_path, use_cache=False, session=FakeSession(FakeResponse(b"", status=404)))

    for n in range(UNAVAILABLE_AFTER + 2):
        with pytest.raises(ClientError):
            f.fetch("Subj.jsp", params={"code": str(n)})

    assert not f.unavailable
    assert f.consecutive_failures == 0


def test_cache_still_serves_after_the_circuit_opens(tmp_path, no_sleep, monkeypatch):
    """已經抓好的頁面沒有理由跟著陪葬。"""
    monkeypatch.setattr(Fetcher._request.retry, "wait", wait_none())

    state = {"fail": False}

    class FlakySession(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if state["fail"]:
                raise requests.ConnectTimeout("connect timed out")
            return FakeResponse("<html>資工四</html>".encode("utf-8"))

    f = Fetcher(cache_dir=tmp_path, session=FlakySession())
    f.fetch("Subj.jsp", params={"code": "2915"})

    state["fail"] = True
    for n in range(UNAVAILABLE_AFTER):
        with pytest.raises(requests.ConnectionError):
            f.fetch("Subj.jsp", params={"code": f"dead{n}"})
    assert f.unavailable

    assert f.fetch("Subj.jsp", params={"code": "2915"}) == "<html>資工四</html>"


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
