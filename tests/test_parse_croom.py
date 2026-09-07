"""解析教室頁(`Croom.jsp?format=-3`)。

只取容量。使用率與週課表刻意不解析 —— 見設計文件的「刻意不做」。
"""

from __future__ import annotations

import pytest

from crawler.parse_croom import parse_classroom
from tests.conftest import load_fixture


@pytest.fixture
def page():
    return load_fixture("croom_page_real.html")


class TestRealPage:
    def test_reads_capacity(self, page):
        assert parse_classroom(page)["capacity"] == 50

    def test_reads_both_names(self, page):
        parsed = parse_classroom(page)
        assert parsed["name"] == "三教307(e)"
        assert parsed["full_name"] == "第三教學大樓307室"


class TestBrokenPages:
    def test_missing_capacity_is_none_not_zero(self):
        """「讀不到」和「零個座位」是兩回事,填 0 會讓使用端算出無限大的使用率。"""
        html = (
            "<table border='1'><tr><th>教室簡稱</th></tr>"
            "<tr><td>三教307(e)</td><td>第三教學大樓307室</td><td>　</td></tr></table>"
        )
        assert parse_classroom(html)["capacity"] is None

    def test_non_numeric_capacity_is_none(self):
        html = (
            "<table border='1'><tr><th>教室簡稱</th></tr>"
            "<tr><td>某教室</td><td>某某室</td><td>不詳</td></tr></table>"
        )
        assert parse_classroom(html)["capacity"] is None

    def test_no_table_at_all(self):
        """學校改版或回了錯誤頁時,不要拋例外 —— 呼叫端要能記錯誤後繼續。"""
        parsed = parse_classroom("<html><body>查無資料</body></html>")
        assert parsed == {"name": None, "full_name": None, "capacity": None}

    def test_too_few_cells(self):
        html = "<table border='1'><tr><td>只有一格</td></tr></table>"
        assert parse_classroom(html)["capacity"] is None
