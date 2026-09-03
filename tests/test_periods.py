"""Phase 2 驗收:節次代碼解析。"""

from __future__ import annotations

import pytest

from crawler.models import REQUIREMENT_SYMBOLS, Course, TimeSlot
from crawler.periods import PERIOD_TIMES, parse_period_cell, period_table


class TestParsePeriodCell:
    def test_typical_three_period_block(self):
        """資工四「數位影像處理」週五的欄位長這樣。"""
        assert parse_period_cell(" 2 3 4") == ["2", "3", "4"]

    def test_full_width_space_is_empty(self):
        """空欄位是全形空白 U+3000,不是空字串。"""
        assert parse_period_cell("\u3000") == []
        assert parse_period_cell("　　") == []

    def test_empty_and_whitespace(self):
        assert parse_period_cell("") == []
        assert parse_period_cell("   \n\t") == []
        assert parse_period_cell("\xa0") == []

    def test_noon_period_n(self):
        assert parse_period_cell(" N") == ["N"]
        assert parse_period_cell(" 4 N 5") == ["4", "N", "5"]

    def test_evening_periods_a_to_d(self):
        assert parse_period_cell(" A B C D") == ["A", "B", "C", "D"]

    def test_mixed_day_and_evening(self):
        """「機器學習」是 8 9 A —— 跨過傍晚分界。"""
        assert parse_period_cell(" 8 9 A") == ["8", "9", "A"]

    def test_lowercase_is_normalised(self):
        assert parse_period_cell(" a b") == ["A", "B"]

    def test_codes_without_separators_still_parse(self):
        """每個代碼都是單一字元,就算沒有分隔空白也不該解錯。"""
        assert parse_period_cell("234") == ["2", "3", "4"]

    def test_unknown_code_is_kept_with_warning(self, caplog):
        """未知代碼保留原字元(不丟資料),但要留下警告。"""
        with caplog.at_level("WARNING"):
            assert parse_period_cell(" Z") == ["Z"]
        assert "未知的節次代碼" in caplog.text

    def test_order_is_preserved_as_written(self):
        assert parse_period_cell(" 9 8") == ["9", "8"]


class TestPeriodTable:
    def test_covers_all_fourteen_codes(self):
        table = period_table()
        assert len(table) == 14
        assert {row["code"] for row in table} == set(PERIOD_TIMES)

    def test_matches_the_table_printed_on_the_page(self):
        table = {row["code"]: (row["start"], row["end"]) for row in period_table()}
        assert table["1"] == ("08:10", "09:00")
        assert table["N"] == ("12:10", "13:00")
        assert table["5"] == ("13:10", "14:00")
        assert table["A"] == ("18:30", "19:20")
        assert table["D"] == ("21:10", "22:00")


class TestRequirementSymbols:
    @pytest.mark.parametrize("symbol", ["★", "☆"])
    def test_both_stars_are_elective(self, symbol):
        """★ 與 ☆ 都是「選」,差別在專業 / 共同(plan.md §7 已解決事項)。"""
        required, _ = REQUIREMENT_SYMBOLS[symbol]
        assert required is False

    @pytest.mark.parametrize("symbol", ["○", "△", "●", "▲"])
    def test_the_other_four_are_required(self, symbol):
        required, _ = REQUIREMENT_SYMBOLS[symbol]
        assert required is True

    def test_labels(self):
        assert REQUIREMENT_SYMBOLS["★"][1] == "專業選修"
        assert REQUIREMENT_SYMBOLS["○"][1] == "部訂共同必修"


class TestTimeSlot:
    def test_day_name(self):
        assert TimeSlot(0, ["1"]).day_name == "日"
        assert TimeSlot(5, ["2"]).day_name == "五"

    def test_to_dict(self):
        assert TimeSlot(5, ["2", "3"]).to_dict() == {
            "day": 5,
            "day_name": "五",
            "periods": ["2", "3"],
        }


class TestCourseMerge:
    def test_merge_unions_list_fields_without_duplicates(self):
        """同一門合開課出現在兩個班級頁時要併成一筆。"""
        a = Course(id="364893", name_zh="數位影像處理", classes=["資工四"],
                   class_ids=["2915"], department_ids=["59"], teachers=["白敦文"])
        b = Course(id="364893", name_zh="數位影像處理", classes=["資工所"],
                   class_ids=["3743"], department_ids=["59"], teachers=["白敦文"])
        a.merge_from(b)

        assert a.classes == ["資工四", "資工所"]
        assert a.class_ids == ["2915", "3743"]
        assert a.department_ids == ["59"]
        assert a.teachers == ["白敦文"]

    def test_merge_keeps_scalar_fields_of_the_first_record(self):
        a = Course(id="1", name_zh="甲", credits=3.0)
        a.merge_from(Course(id="1", name_zh="乙", credits=9.0))
        assert (a.name_zh, a.credits) == ("甲", 3.0)
