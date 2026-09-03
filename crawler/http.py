"""全專案**唯一**對外發出 HTTP 請求的模組。

其他模組(尤其是 parse_*.py)一律不得自行連網。這樣限速、快取、重試
的規則只要在這裡守住,整個專案就守住了。

plan.md §1.4 的硬性規定在這裡實作:
- 單執行緒,不平行抓取
- 每次真正發出請求後強制 sleep(下限 0.5 秒)
- User-Agent 帶辨識與聯絡方式
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from urllib.parse import urljoin

from .config import (
    BASE_URL,
    CACHE_DIR,
    DEFAULT_DELAY,
    MIN_DELAY,
    TIMEOUT,
    USER_AGENT,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# SSL:必須在 import requests 之前注入 truststore(plan.md §1.5)
#
# 學校憑證缺少 Subject Key Identifier,OpenSSL 3.x 嚴格模式會擋掉,但
# curl(schannel)與瀏覽器都能通過。truststore 讓 Python 改用作業系統
# 憑證庫驗證,行為與瀏覽器一致 —— 這是「解掉」而不是 verify=False 那種
# 「蓋掉」。
#
# ❓plan.md §7-3:Linux + Python 3.12 可能根本不需要,也可能反而出錯。
# 因此這裡做成「盡力注入,失敗就記 warning 繼續」,並提供環境變數
# NTUT_DISABLE_TRUSTSTORE=1 可完全停用。
# --------------------------------------------------------------------------
def _inject_truststore() -> bool:
    if os.environ.get("NTUT_DISABLE_TRUSTSTORE") == "1":
        log.info("NTUT_DISABLE_TRUSTSTORE=1,略過 truststore 注入")
        return False
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception as exc:  # pragma: no cover - 環境相依
        log.warning("truststore 注入失敗(%s),改用 Python 內建憑證驗證", exc)
        return False


TRUSTSTORE_ACTIVE = _inject_truststore()

import requests  # noqa: E402  (必須在 truststore 注入之後)
from tenacity import (  # noqa: E402
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class FetchError(Exception):
    """抓取失敗的基底例外。"""


class ClientError(FetchError):
    """4xx。重試沒有意義,而且可能代表被擋,直接往上拋。"""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status} (不重試): {url}")
        self.status = status
        self.url = url


class ServerError(FetchError):
    """5xx。可重試。"""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"HTTP {status}: {url}")
        self.status = status
        self.url = url


def resolve_delay(delay: float | None = None) -> float:
    """決定實際延遲秒數,並強制套用下限。

    優先序:參數 > 環境變數 CRAWL_DELAY > DEFAULT_DELAY。
    低於 MIN_DELAY 一律拉回下限 —— 這條規則不接受被關掉。
    """
    if delay is None:
        raw = os.environ.get("CRAWL_DELAY")
        if raw is not None:
            try:
                delay = float(raw)
            except ValueError:
                log.warning("CRAWL_DELAY=%r 不是數字,改用預設 %.1fs", raw, DEFAULT_DELAY)
                delay = DEFAULT_DELAY
        else:
            delay = DEFAULT_DELAY

    if delay < MIN_DELAY:
        log.warning("要求的延遲 %.2fs 低於下限,強制拉回 %.2fs", delay, MIN_DELAY)
        return MIN_DELAY
    return delay


class Fetcher:
    """帶快取、限速、重試的單執行緒抓取器。"""

    def __init__(
        self,
        *,
        delay: float | None = None,
        use_cache: bool = True,
        cache_dir: str | os.PathLike[str] = CACHE_DIR,
        session: "requests.Session | None" = None,
    ) -> None:
        self.delay = resolve_delay(delay)
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir)
        # 單一 Session:站台會發 JSESSIONID,cookie 要保留
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        # 統計,給結束摘要用
        self.request_count = 0
        self.cache_hit_count = 0

    # -- 快取 ---------------------------------------------------------------
    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.html", self.cache_dir / f"{key}.url"

    def _cache_read(self, url: str) -> str | None:
        if not self.use_cache:
            return None
        html_path, _ = self._cache_paths(url)
        if not html_path.is_file():
            return None
        try:
            return html_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁碟異常
            log.warning("讀取快取失敗(%s),改為重新抓取:%s", exc, url)
            return None

    def _cache_write(self, url: str, html: str) -> None:
        if not self.use_cache:
            return
        html_path, url_path = self._cache_paths(url)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
            # 附一個純文字檔記錄原始 URL,方便人工翻查 .cache/
            url_path.write_text(url, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁碟異常
            log.warning("寫入快取失敗(%s):%s", exc, url)

    # -- 實際請求 -----------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(
            (ServerError, requests.Timeout, requests.ConnectionError)
        ),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _request(self, url: str) -> str:
        """發出一次請求並回傳解碼後的 HTML。失敗時由 tenacity 退避重試。"""
        resp = self.session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if 400 <= resp.status_code < 500:
            raise ClientError(resp.status_code, url)
        if resp.status_code >= 500:
            raise ServerError(resp.status_code, url)
        # 站台實際是 UTF-8,但 header 之外的自動判斷不可信,固定手動解碼
        return resp.content.decode("utf-8")

    # -- 對外入口 -----------------------------------------------------------
    def build_url(self, url: str, params: dict | None = None) -> str:
        """把相對路徑 + query 組成正規化的絕對 URL(也是快取鍵)。"""
        absolute = urljoin(BASE_URL, url)
        return requests.Request("GET", absolute, params=params).prepare().url

    def fetch(self, url: str, *, params: dict | None = None) -> str:
        """回傳已正確解碼的 HTML 字串。

        命中快取時不發請求、不 sleep。
        """
        full_url = self.build_url(url, params)

        cached = self._cache_read(full_url)
        if cached is not None:
            self.cache_hit_count += 1
            log.debug("快取命中:%s", full_url)
            return cached

        log.info("GET %s", full_url)
        html = self._request(full_url)
        self.request_count += 1
        self._cache_write(full_url, html)

        # sleep 放在請求「之後」:先付出等待,才輪到下一個請求
        time.sleep(self.delay)
        return html


# --------------------------------------------------------------------------
# 模組層預設實例。小型腳本可直接 `from crawler.http import fetch`。
# main.py 則自行建立 Fetcher 以套用 CLI 參數。
# --------------------------------------------------------------------------
_default: Fetcher | None = None


def get_fetcher() -> Fetcher:
    global _default
    if _default is None:
        _default = Fetcher()
    return _default


def configure(**kwargs) -> Fetcher:
    """重建模組層預設 Fetcher(參數同 Fetcher.__init__)。"""
    global _default
    _default = Fetcher(**kwargs)
    return _default


def fetch(url: str, *, params: dict | None = None) -> str:
    """回傳已正確解碼的 HTML 字串。"""
    return get_fetcher().fetch(url, params=params)
