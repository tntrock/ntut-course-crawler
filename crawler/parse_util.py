"""解析器共用的小工具。"""

from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .config import BASE_URL

#: 空欄位在這個站台是全形空白 U+3000,不是空字串。
#: 只 strip() 會得到「看起來是空、長度卻是 1」的字串,一定要一起清掉。
_STRIP_CHARS = " \t\r\n\u3000\xa0"


def soup_of(html: str) -> BeautifulSoup:
    """一律用 lxml。

    這個站台的 <tr>/<td> 都沒有收尾標籤(老式 JSP 輸出),
    html.parser 會把巢狀結構切錯,lxml 才能還原成正確的表格。
    """
    return BeautifulSoup(html, "lxml")


def clean(text: str | None) -> str | None:
    """正規化欄位文字。空欄位(含全形空白)回傳 None。"""
    if text is None:
        return None
    stripped = text.strip(_STRIP_CHARS)
    return stripped or None


def query_param(url: str, name: str) -> str | None:
    """取出 URL query 中的某個參數值。"""
    values = parse_qs(urlparse(url).query).get(name)
    if not values:
        return None
    return values[0].strip() or None


def absolute_url(href: str) -> str:
    """把頁面上的相對連結補成絕對 URL。"""
    return urljoin(BASE_URL, href)
