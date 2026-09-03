"""解析課程系統首頁,找出目前開放的學年期。

對真實樣本 course_home_real.html 斷言 —— 學校改版時這裡會先紅。
"""

from __future__ import annotations

from crawler.models import Semester
from crawler.parse_semester import parse_semesters


class TestRealHomePage:
    def test_finds_both_semesters(self, fixture):
        found = parse_semesters(fixture("course_home_real.html"))
        assert found == [Semester(115, 1), Semester(114, 2)]

    def test_newest_first(self, fixture):
        found = parse_semesters(fixture("course_home_real.html"))
        assert found == sorted(found, reverse=True)

    def test_path_is_the_output_directory_name(self, fixture):
        assert parse_semesters(fixture("course_home_real.html"))[0].path == "115-1"


class TestFiltering:
    """首頁上其他連結也帶 year/sem,不能一起收進來。"""

    def test_ignores_other_scripts_with_year_and_sem(self):
        html = """
        <a href="SearchProgram.jsp?format=-1&year=113&sem=1">學程查詢</a>
        <a href="Teach.jsp?format=-1&year=113&sem=1">教師授課時數表</a>
        <a href="Croom.jsp?format=-2&year=113&sem=1">教室使用情形</a>
        <a href="Summer.jsp?format=-1&year=113">暑期課程</a>
        <a href="Subj.jsp?format=-2&year=113&sem=1">上課時間表</a>
        """
        assert parse_semesters(html) == [Semester(113, 1)]

    def test_ignores_subj_with_other_formats(self):
        html = """
        <a href="Subj.jsp?format=-3&year=113&sem=1&code=59">資工系</a>
        <a href="Subj.jsp?format=-4&year=113&sem=1&code=2915">資工四</a>
        """
        assert parse_semesters(html) == []

    def test_deduplicates_repeated_links(self):
        html = """
        <a href="Subj.jsp?format=-2&year=115&sem=1">上課時間表</a>
        <a href="Subj.jsp?format=-2&year=115&sem=1">同一個學期又出現一次</a>
        """
        assert parse_semesters(html) == [Semester(115, 1)]


class TestDegradation:
    """一個壞連結不該讓整批失敗。"""

    def test_skips_links_without_year_or_sem(self, caplog):
        html = """
        <a href="Subj.jsp?format=-2&sem=1">缺 year</a>
        <a href="Subj.jsp?format=-2&year=115">缺 sem</a>
        <a href="Subj.jsp?format=-2&year=115&sem=1">正常</a>
        """
        assert parse_semesters(html) == [Semester(115, 1)]
        assert "缺少 year/sem" in caplog.text

    def test_skips_non_numeric_year(self):
        html = '<a href="Subj.jsp?format=-2&year=abc&sem=1">壞掉</a>'
        assert parse_semesters(html) == []

    def test_empty_page_warns_instead_of_raising(self, caplog):
        assert parse_semesters("<html><body>沒有連結</body></html>") == []
        assert "學校可能改版" in caplog.text
