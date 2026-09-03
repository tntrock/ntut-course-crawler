"""Phase 3 驗收:課程列表頁解析。

主要斷言對象是 tests/fixtures/course_list_real.html(資工四)。
另外用小段合成 HTML 涵蓋 fixture 沒有的邊界情況(必修符號、跨日課、壞資料)。
"""

from __future__ import annotations

import pytest

from crawler.parse_course import COLUMN_COUNT, parse_courses


@pytest.fixture
def courses(fixture):
    return parse_courses(fixture("course_list_real.html"))


@pytest.fixture
def by_id(courses):
    return {c.id: c for c in courses}


def build_page(*rows: str, class_name: str = "測試班") -> str:
    """組一頁最小可解析的課程列表,用來測 fixture 沒涵蓋到的情況。"""
    header = "<th>".join(
        [
            "課號", "課程名稱", "階段", "學分", "時數", "修", "教師",
            "日", "一", "二", "三", "四", "五", "六",
            "教室", "人", "撤", "授課語言", "教學大綱", "備註",
            "隨班附讀", "實驗實習", "跨領域",
        ]
    )
    body = "".join(rows)
    return (
        "<html><body><table border=1>"
        f"<tr><th colspan=23>{class_name}"
        f"<tr><th>{header}"
        f"{body}</table></body></html>"
    )


def row(**overrides: str) -> str:
    """產生一列 23 欄的課程,未指定的欄位是全形空白。"""
    cells = ["　"] * COLUMN_COUNT
    names = {
        "id": 0, "name": 1, "stage": 2, "credits": 3, "hours": 4,
        "requirement": 5, "teacher": 6,
        "sun": 7, "mon": 8, "tue": 9, "wed": 10, "thu": 11, "fri": 12, "sat": 13,
        "classroom": 14, "quota": 15, "withdrawn": 16, "language": 17,
        "syllabus": 18, "notes": 19, "audit": 20, "lab": 21, "programs": 22,
    }
    for key, value in overrides.items():
        cells[names[key]] = value
    return "<tr><td>" + "<td>".join(cells)


class TestFixtureCourses:
    def test_course_count(self, courses):
        """6 門課 —— 班週會列與小計列都不算。"""
        assert len(courses) == 6

    def test_weekly_meeting_row_is_skipped(self, courses):
        assert all(c.name_zh != "班週會及導師時間" for c in courses)

    def test_subtotal_row_is_skipped(self, courses):
        assert all(c.name_zh != "小計" and c.id != "小計" for c in courses)

    def test_flagship_course_fields(self, by_id):
        """plan.md 指定的驗收條件。"""
        c = by_id["364893"]
        assert c.name_zh == "數位影像處理"
        assert c.credits == 3.0
        assert c.teachers == ["白敦文"]
        assert c.time_slots[0].day == 5 and c.time_slots[0].day_name == "五"
        assert c.time_slots[0].periods == ["2", "3", "4"]
        assert c.classrooms == ["六教727(e)"]

    def test_only_days_with_class_produce_a_time_slot(self, by_id):
        assert len(by_id["364893"].time_slots) == 1

    def test_teacher_code_is_captured_for_syllabus_url(self, by_id):
        c = by_id["364893"]
        assert c.teacher_codes == ["12095"]
        assert c.syllabus_url == (
            "https://aps.ntut.edu.tw/course/tw/ShowSyllabus.jsp?snum=364893&code=12095"
        )

    def test_classroom_code_is_captured(self, by_id):
        assert by_id["364893"].classroom_codes == ["452"]

    def test_class_name_comes_from_the_table_header(self, courses):
        assert all(c.classes == ["資工四"] for c in courses)

    def test_star_is_elective_not_required(self, by_id):
        """★ 是專業選修。這一頁全部都是選修課。"""
        c = by_id["364893"]
        assert c.required is False
        assert c.requirement_type == "專業選修"

    def test_hollow_star_is_also_elective(self, by_id):
        """☆ 也是選,差別只在共同 / 專業。"""
        c = by_id["361339"]
        assert c.required is False
        assert c.requirement_type == "共同選修"

    def test_programs_are_split_on_br(self, by_id):
        assert by_id["364893"].programs == [
            "人工智慧科技學程",
            "光電智慧製造學程",
            "人工智慧與深度學習微學程",
            "太空科技微學程",
        ]

    def test_empty_program_cell_is_empty_list(self, by_id):
        assert by_id["361351"].programs == []

    def test_language_blank_means_chinese(self, by_id):
        """全形空白要被正規化成 None,不是長度 1 的字串。"""
        assert by_id["364893"].language is None
        assert by_id["361345"].language == "英語"

    def test_quota_and_withdrawn(self, by_id):
        assert by_id["364893"].quota == 22
        assert by_id["364893"].withdrawn == 0
        assert by_id["361339"].quota == 0

    def test_notes(self, by_id):
        assert by_id["364893"].notes == "資工四和資工所合開"
        assert by_id["361339"].notes is None

    def test_course_without_teacher_or_time(self, by_id):
        """體育這種課沒有教師、沒有時間、沒有教室,不該壞掉。"""
        c = by_id["361339"]
        assert c.name_zh == "體育"
        assert c.teachers == [] and c.classrooms == [] and c.time_slots == []
        assert c.syllabus_url is None

    def test_classroom_name_with_inner_space_is_preserved(self, by_id):
        """證明多值欄位不能用空白切:教室名稱本身含空白。"""
        assert by_id["361368"].classrooms == ["先鋒401 (e)"]

    def test_evening_period_course(self, by_id):
        assert by_id["364892"].time_slots[0].periods == ["8", "9", "A"]

    def test_english_name_has_no_source(self, courses):
        """plan.md §7-2:課程列表頁與教學大綱頁都沒有英文課名。"""
        assert all(c.name_en is None for c in courses)


class TestRequirementSymbols:
    @pytest.mark.parametrize(
        "symbol,required,label",
        [
            ("○", True, "部訂共同必修"),
            ("△", True, "校訂共同必修"),
            ("☆", False, "共同選修"),
            ("●", True, "部訂專業必修"),
            ("▲", True, "校訂專業必修"),
            ("★", False, "專業選修"),
        ],
    )
    def test_all_six_symbols(self, symbol, required, label):
        cell = f'<A href="Cprog.jsp?format=-5">{symbol}</A>'
        c = parse_courses(build_page(row(id="123456", name="測試", requirement=cell)))[0]
        assert c.required is required
        assert c.requirement_type == label

    def test_blank_requirement_is_none_not_false(self):
        """空欄位不可預設為「選修」。"""
        c = parse_courses(build_page(row(id="123456", name="測試")))[0]
        assert c.required is None
        assert c.requirement_type is None

    def test_unknown_symbol_warns_and_returns_none(self, caplog):
        html = build_page(row(id="123456", name="測試", requirement="◆"))
        with caplog.at_level("WARNING"):
            c = parse_courses(html)[0]
        assert c.required is None
        assert "未知的必選修符號" in caplog.text


class TestDegradedInput:
    def test_row_with_wrong_column_count_is_skipped_with_warning(self, caplog):
        broken = "<tr><td>999999<td>欄位不夠的課<td>1"
        html = build_page(broken, row(id="123456", name="正常課"))
        with caplog.at_level("WARNING"):
            courses = parse_courses(html)
        assert [c.id for c in courses] == ["123456"]
        assert "欄數" in caplog.text

    def test_non_numeric_values_degrade_to_none(self, caplog):
        html = build_page(row(id="123456", name="測試", credits="待定", quota="不限"))
        with caplog.at_level("WARNING"):
            c = parse_courses(html)[0]
        assert c.credits is None and c.quota is None

    def test_page_without_course_table(self):
        assert parse_courses("<html><body>本班無課程</body></html>") == []

    def test_period_legend_table_is_not_mistaken_for_courses(self, fixture):
        """頁尾還有一張節次對照表,不能被當成課程表格。"""
        assert len(parse_courses(fixture("course_list_real.html"))) == 6


class TestMultiValueCells:
    def test_multiple_teachers_split_on_br(self):
        teacher = (
            '<A href="Teach.jsp?format=-3&year=115&sem=1&code=111">甲老師</A><BR>'
            '<A href="Teach.jsp?format=-3&year=115&sem=1&code=222">乙老師</A><BR>'
        )
        c = parse_courses(build_page(row(id="123456", name="合授課", teacher=teacher)))[0]
        assert c.teachers == ["甲老師", "乙老師"]
        assert c.teacher_codes == ["111", "222"]

    def test_teacher_without_link_still_captured(self):
        c = parse_courses(build_page(row(id="123456", name="測試", teacher="外聘講師")))[0]
        assert c.teachers == ["外聘講師"]
        assert c.teacher_codes == [""]

    def test_course_meeting_on_several_days(self):
        c = parse_courses(
            build_page(row(id="123456", name="每週兩次", mon=" 1 2", thu=" N"))
        )[0]
        assert [(s.day, s.periods) for s in c.time_slots] == [(1, ["1", "2"]), (4, ["N"])]
