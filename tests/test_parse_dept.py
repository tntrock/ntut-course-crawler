"""Phase 3 驗收:總覽頁與單位頁解析。

全部對 Phase 0 存下的真實 fixture 斷言。學校改版時這些測試會先失敗,
這是刻意的預警機制(plan.md §3 Phase 3)。
"""

from __future__ import annotations

import pytest

from crawler.parse_dept import parse_class_groups, parse_colleges


@pytest.fixture
def departments(fixture):
    return parse_colleges(fixture("subj_overview.html"))


@pytest.fixture
def by_name(departments):
    return {d.name: d for d in departments}


class TestParseColleges:
    def test_finds_all_units(self, departments):
        assert len(departments) == 60

    def test_ids_are_unique(self, departments):
        ids = [d.id for d in departments]
        assert len(ids) == len(set(ids))

    def test_computer_science_department(self, by_name):
        """plan.md 指定的驗收條件:資工系 → 電資學院。"""
        dept = by_name["資工系"]
        assert dept.id == "59"
        assert dept.college == "電資學院"
        assert dept.url.endswith("Subj.jsp?format=-3&year=115&sem=1&code=59")

    def test_administrative_units_have_no_college(self, by_name):
        """第一列是行政單位,學院欄是全形空白 → None,不是空字串。"""
        for name, code in [("教務處", "01"), ("體育室", "10"), ("通識中心", "14"),
                           ("師資培育中心", "62"), ("校院級課程", "AA")]:
            dept = by_name[name]
            assert dept.id == code
            assert dept.college is None

    def test_rowspan_is_followed_across_continuation_rows(self, by_name):
        """學院名稱只出現一次,靠 rowspan 涵蓋後續列。"""
        # 機電學院 rowspan=3,這三個分別在第 1/2/3 列
        assert by_name["智動科"].college == "機電學院"          # 標題列
        assert by_name["製科所"].college == "機電學院"          # 延續列 1
        assert by_name["半導體外生專班"].college == "機電學院"  # 延續列 2

    def test_department_named_like_a_college_in_a_continuation_row(self, by_name):
        """陷阱:延續列的第一格就是一個系所連結,名稱還跟學院一樣。"""
        dept = by_name["管理學院"]
        assert dept.id == "C2"
        assert dept.college == "管理學院"

    def test_separator_rows_do_not_leak_a_college(self, by_name):
        """學院區塊之間的 <tr><td colspan=6> 空白列不該產生單位。"""
        assert by_name["創新學院"].college == "創新前瞻科技研究學院"

    def test_every_college_seen_on_the_page(self, departments):
        colleges = {d.college for d in departments if d.college}
        assert colleges == {
            "機電學院", "工程學院", "管理學院", "設計學院",
            "人文與社會科學學院", "電資學院", "創新前瞻科技研究學院",
        }

    def test_urls_are_absolute(self, departments):
        assert all(d.url.startswith("https://aps.ntut.edu.tw/") for d in departments)

    def test_empty_html_degrades_instead_of_raising(self):
        assert parse_colleges("<html><body>維護中</body></html>") == []


class TestParseClassGroups:
    def test_real_department(self, fixture):
        """plan.md 指定的驗收條件:資工系 5 個班級,含資工四 / 2915。"""
        groups = parse_class_groups(fixture("dept_page_real.html"), "59")
        assert len(groups) == 5
        assert [g.name for g in groups] == ["資工四", "資工三", "資工二", "資工一", "資工所"]

        senior = groups[0]
        assert senior.id == "2915"
        assert senior.department_id == "59"
        assert senior.url.endswith("Subj.jsp?format=-4&year=115&sem=1&code=2915")

    def test_class_code_is_not_derivable_from_department_code(self, fixture):
        """班級代碼是伺服器另配的 ID,一定要從頁面解析,不能自己拼。"""
        groups = parse_class_groups(fixture("dept_page_real.html"), "59")
        assert all(g.id != g.department_id for g in groups)
        assert {g.id for g in groups} == {"2915", "3032", "3138", "3718", "3743"}

    def test_administrative_unit(self, fixture):
        """行政單位底下是班群而不是系所班級,結構一樣可解。"""
        groups = parse_class_groups(fixture("dept_page.html"), "01")
        assert [g.name for g in groups] == [
            "遠距教學班(大學部)", "輔導課程", "特殊學生專班課程", "遠距教學班(研究所)",
        ]
        assert groups[0].id == "450"
        assert all(g.department_id == "01" for g in groups)

    def test_empty_page_degrades_instead_of_raising(self):
        assert parse_class_groups("<html><body>無課程</body></html>", "99") == []
