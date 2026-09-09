"""班級課表頁回一頁「沒有課程表格」的內容,不可以讓底下的課被判成停開。

線上實際發生過三次,一次比一次清楚:

    2026-09-08 05:36  班級 3777(技職教育研究所)13 門課同時「停開」
    2026-09-09 05:41  班級 3041(互動設計系外生三)6 門課同時「停開」

第二次的執行紀錄把根因寫死了:

    GET .../Subj.jsp?format=-4&code=3041&year=115&sem=1
    WARNING crawler.parse_course: 課程頁找不到課程表格,回傳空清單

那一輪 departments_ok=60、errors=0、`classes.json` 51 個學期全數還原成功 ——
**沒有任何東西失敗**,班級也確實被抓了,只是學校回的那一頁根本不是課表頁。
`parse_courses()` 把「頁面上沒有課程表格」跟「這個班級沒有課」當成同一件事
回傳空清單,那 6 門課就這樣從 `result.courses` 消失,再被 `_write_changes()`
記成 6 筆 `course_removed`。下一輪抓回來時又記成 6 筆 `course_added`。

這跟 `test_missing_class_groups.py` 是**同一個 bug 的另一半**:那邊守的是
「單位頁少列班級」(班級沒被抓到),這邊守的是「班級抓到了但頁面壞掉」。

分辨的依據是**課程表格在不在**,不是課數是不是 0:
表頭那一列(`課號` `課程名稱` `階段` …)是靜態版面的一部分,真的沒有課的
班級照樣會渲染出來。所以「整頁找不到 `課號` 表頭」只可能是頁面壞了 ——
用它當判準,真的被學校撤空的班級不會被誤判,也就不會卡在永遠重試。
"""

from __future__ import annotations

import json

import pytest

from crawler.main import IncompleteCrawl, crawl, main
from crawler.parse_course import CourseTableMissing, parse_courses
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401
from tests.test_parse_course import build_page

BROKEN_PAGE = "<html><body>本班無課程</body></html>"

#: dept_page_real.html 裡資工系(59)的五個班級
ALL_GROUPS = {"2915", "3032", "3138", "3718", "3743"}
BROKEN = "3743"


class TestParseTellsBrokenPageFromEmptyClass:
    """解析層先分得出來,上面才有東西可以守。"""

    def test_a_page_without_the_course_table_raises(self):
        with pytest.raises(CourseTableMissing):
            parse_courses(BROKEN_PAGE)

    def test_the_error_says_which_shape_was_missing(self):
        with pytest.raises(CourseTableMissing, match="課號"):
            parse_courses(BROKEN_PAGE)

    def test_a_real_table_with_no_course_rows_is_just_empty(self):
        """真的沒有課的班級 —— 表頭還在,那是合法的 0 門,不是壞頁面。"""
        assert parse_courses(build_page()) == []

    def test_a_table_with_only_non_course_rows_is_just_empty(self):
        """班週會與小計不是課。整頁只剩它們也還是合法的 0 門。"""
        html = build_page("<tr><td><td>班週會及導師時間", "<tr><td>小計<td>0")
        assert parse_courses(html) == []


class TestABrokenPageIsRetried:
    def test_a_page_that_recovers_on_the_second_try_loses_nothing(self):
        fetcher = FakeFetcher(break_class_pages={BROKEN: 1})
        result = crawl(fetcher, 115, 1, only_departments=["59"])
        assert BROKEN in {cid for c in result.courses for cid in c.class_ids}, (
            "重抓成功了,課卻沒有回到結果裡"
        )

    def test_the_cached_copy_of_the_broken_page_is_dropped(self):
        """壞掉那一份留在 `.cache/` 裡,workflow 的重試迴圈會一路讀到它。

        重試跟第一次跑在同一個 job,快取是共用的 —— 不丟掉這一份,五次
        重試會全部讀到同一頁壞掉的 HTML,全部失敗。
        """
        fetcher = FakeFetcher(break_class_pages={BROKEN: 1})
        crawl(fetcher, 115, 1, only_departments=["59"])
        assert BROKEN in fetcher.invalidated

    def test_a_healthy_page_is_never_refetched(self):
        fetcher = FakeFetcher()
        crawl(fetcher, 115, 1, only_departments=["59"])
        codes = [code for fmt, code in fetcher.calls if fmt == -4]
        assert len(codes) == len(set(codes)) == len(ALL_GROUPS)
        assert fetcher.invalidated == []


class TestAPersistentlyBrokenPageAbortsTheSemester:
    """重抓完還是壞 —— 那就不知道這個班級有哪些課,不能當成「它沒有課」。

    守則跟 `SiteUnavailable` 同源(見 `crawl()` 的註解:「把這種半套結果
    寫出去會蓋掉線上完整的資料」),只是下沉到班級層級。
    """

    def test_the_whole_semester_is_raised_not_silently_shortened(self):
        fetcher = FakeFetcher(break_class_pages={BROKEN: 99})
        with pytest.raises(IncompleteCrawl):
            crawl(fetcher, 115, 1, only_departments=["59"])

    def test_the_message_names_the_class_group(self):
        fetcher = FakeFetcher(break_class_pages={BROKEN: 99})
        with pytest.raises(IncompleteCrawl, match=BROKEN):
            crawl(fetcher, 115, 1, only_departments=["59"])

    def test_the_other_class_groups_are_still_crawled_first(self):
        """一個班級壞掉不該讓同一輪的其他班級白跑 —— 快取要熱,重試才便宜。"""
        fetcher = FakeFetcher(break_class_pages={BROKEN: 99})
        with pytest.raises(IncompleteCrawl):
            crawl(fetcher, 115, 1, only_departments=["59"])
        assert {code for fmt, code in fetcher.calls if fmt == -4} == ALL_GROUPS

    def test_it_is_retried_the_full_number_of_attempts(self):
        fetcher = FakeFetcher(break_class_pages={BROKEN: 99})
        with pytest.raises(IncompleteCrawl):
            crawl(fetcher, 115, 1, only_departments=["59"])
        tries = [code for fmt, code in fetcher.calls if fmt == -4 and code == BROKEN]
        assert len(tries) > 1


class TestNoPhantomRemovalReachesTheOutput:
    """整條接起來:第一輪正常,第二輪頁面壞掉,異動頁不可以出現停開。"""

    def run_once(self, tmp_path):
        return main(
            ["--year", "115", "--sem", "1", "--out", str(tmp_path),
             "--dept", "59", "--log-level", "CRITICAL"]
        )

    def events(self, tmp_path):
        path = tmp_path / "changes.json"
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["events"]

    def test_the_second_run_records_no_removal(self, tmp_path, fake_fetcher_factory):
        self.run_once(tmp_path)
        before = self.events(tmp_path)

        fake_fetcher_factory.break_class_pages = {BROKEN: 99}
        self.run_once(tmp_path)

        new = self.events(tmp_path)[: len(self.events(tmp_path)) - len(before)]
        assert [e for e in new if e["type"] == "course_removed"] == [], (
            "頁面壞掉被記成停開了 —— 這就是線上那兩批假停開的來源"
        )

    def test_the_second_run_reports_failure_so_the_workflow_retries(
        self, tmp_path, fake_fetcher_factory
    ):
        self.run_once(tmp_path)
        fake_fetcher_factory.break_class_pages = {BROKEN: 99}
        assert self.run_once(tmp_path) == 1

    def test_the_published_index_keeps_the_previous_courses(
        self, tmp_path, fake_fetcher_factory
    ):
        """不輸出這一輪 = 線上那份原封不動(發布用 keep_files)。"""
        self.run_once(tmp_path)
        before = (tmp_path / "index.json").read_text(encoding="utf-8")

        fake_fetcher_factory.break_class_pages = {BROKEN: 99}
        self.run_once(tmp_path)

        assert (tmp_path / "index.json").read_text(encoding="utf-8") == before

    def test_the_failure_is_recorded_in_errors_json(
        self, tmp_path, fake_fetcher_factory
    ):
        self.run_once(tmp_path)
        fake_fetcher_factory.break_class_pages = {BROKEN: 99}
        self.run_once(tmp_path)

        errors = json.loads((tmp_path / "errors.json").read_text(encoding="utf-8"))
        assert any(
            e.get("stage") == "semester" and BROKEN in e.get("error", "")
            for e in errors["errors"]
        ), "查無對證的靜默正是這個 bug 最貴的部分"
