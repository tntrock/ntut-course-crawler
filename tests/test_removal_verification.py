"""停開要有正面證據,不能只靠「這一輪沒抓到」。

異動偵測的停開判定一直是**用缺席推論**的:上一輪的索引有、這一輪的結果
沒有,就記一筆 `course_removed`。缺席有兩種原因,而它們在輸出上長得一模
一樣 —— 課真的停開了,或者我們這一輪讀錯了。線上兩批假停開都是後者
(2026-09-08 班級 3777 十三門、2026-09-09 班級 3041 六門)。

學校的教學大綱頁提供了正面證據。2026-09-09 實抓四頁確認:

    ShowSyllabus.jsp?snum=364585   真停開 → 錯誤訊息 : 查無課號 (364585) 的開課資料
    ShowSyllabus.jsp?snum=362717   真停開 → 同上(第二個樣本)
    ShowSyllabus.jsp?snum=362160   還在   → 課程基本資料列出 115-1 362160 互動設計創作(一)

兩個實測到的關鍵性質:

1. **`code` 參數不影響課程基本資料。** `snum=364585&code=11494` 與
   `snum=364585&code=11969`(別門課的 code)回傳的頁面逐字相同。`code` 只
   決定要不要顯示**大綱內容** —— 不給 code 時,課程基本資料照樣完整渲染,
   下面的大綱區塊變成「尚未登錄」。所以查存在只需要課號,不必存
   `syllabus_url`,也不必知道 code。
2. **判準是課程基本資料那一列,不是有沒有大綱內容。** 不給 code 的頁面
   跟停開的頁面都顯示「尚未登錄」,拿大綱內容當判準會把還在的課判成停開。

課表頁(`format=-4`)與大綱頁讀的是**同一份開課資料**。所以「大綱頁查得到、
課表頁沒列到」在邏輯上互相矛盾 —— 那不是停開,是我們這一輪讀錯了,不能
拿去覆蓋線上那份(處置與 `IncompleteCrawl` 相同,見 crawler/main.py)。
"""

from __future__ import annotations

import json

import pytest

from crawler.main import IncompleteCrawl, crawl, main
from crawler.parse_syllabus import course_exists
from tests.conftest import load_fixture
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401

#: course_list_real.html(資工四)那 6 門課,五個班級各回同一份。
FIXTURE_COURSE_IDS = {
    "361339", "364893", "361345", "361351", "364892", "361368",
}


class TestCourseExists:
    """三份真實樣本:停開的、還在但沒帶 code 的、還在且大綱完整的。"""

    def test_a_removed_course_is_reported_gone(self):
        html = load_fixture("syllabus_course_gone.html")
        assert course_exists(html, "364585") is False

    def test_the_error_message_names_the_course(self):
        """判定要對得上課號 —— 拿別門課的頁面來不能算數。"""
        html = load_fixture("syllabus_course_gone.html")
        assert course_exists(html, "999999") is None

    def test_a_live_course_is_reported_present(self):
        html = load_fixture("syllabus_basic_only.html")
        assert course_exists(html, "362160") is True

    def test_a_live_course_without_syllabus_content_is_still_present(self):
        """不給 code 的頁面大綱區塊是「尚未登錄」,但課還在。

        拿「有沒有大綱內容」當判準的話,這一頁會被判成停開。
        """
        html = load_fixture("syllabus_basic_only.html")
        assert "尚未登錄" in html
        assert course_exists(html, "362160") is True

    def test_a_full_syllabus_page_is_present_too(self):
        html = load_fixture("syllabus_page_real.html")
        assert course_exists(html, "364893") is True

    def test_a_page_of_the_wrong_shape_is_undecidable(self):
        """判不出來要說判不出來,不可以預設成「停開」。"""
        assert course_exists("<html><body>維護中</body></html>", "364893") is None


class TestRemovalsAreVerifiedBeforeBeingPublished:
    def run(self, tmp_path):
        # 刻意不加 --dept:局部抓取的結果是 partial,`_write_changes()` 會整個
        # 略過變更紀錄,那樣「沒有停開事件」的斷言會是空的、驗不到東西。
        return main(
            ["--year", "115", "--sem", "1", "--out", str(tmp_path),
             "--log-level", "CRITICAL"]
        )

    def events(self, tmp_path):
        path = tmp_path / "changes.json"
        return json.loads(path.read_text(encoding="utf-8"))["events"] \
            if path.is_file() else []

    def test_a_course_the_school_still_lists_is_never_a_removal(
        self, tmp_path, fake_fetcher_factory
    ):
        """課表頁少了一門,但大綱頁說課還在 → 是我們漏抓,不是停開。"""
        self.run(tmp_path)
        before = len(self.events(tmp_path))

        fake_fetcher_factory.hide_courses = {"364893"}
        self.run(tmp_path)

        new = self.events(tmp_path)[: len(self.events(tmp_path)) - before]
        assert [e for e in new if e["type"] == "course_removed"] == []

    def test_and_that_round_is_not_published_at_all(
        self, tmp_path, fake_fetcher_factory
    ):
        self.run(tmp_path)
        before = (tmp_path / "index.json").read_text(encoding="utf-8")

        fake_fetcher_factory.hide_courses = {"364893"}
        assert self.run(tmp_path) == 1
        assert (tmp_path / "index.json").read_text(encoding="utf-8") == before

    def test_a_course_the_school_no_longer_has_is_a_real_removal(
        self, tmp_path, fake_fetcher_factory
    ):
        """學校說查無課號 → 真的停開,照常記事件、照常發布。"""
        self.run(tmp_path)
        before = len(self.events(tmp_path))

        fake_fetcher_factory.hide_courses = {"364893"}
        fake_fetcher_factory.gone_courses = {"364893"}
        assert self.run(tmp_path) == 0

        new = self.events(tmp_path)[: len(self.events(tmp_path)) - before]
        removed = [e for e in new if e["type"] == "course_removed"]
        assert [e["id"] for e in removed] == ["364893"]

    def test_an_undecidable_page_is_treated_as_not_proven(
        self, tmp_path, fake_fetcher_factory
    ):
        """判不出來就是沒證據,沒證據就不記停開、也不發布。"""
        self.run(tmp_path)
        fake_fetcher_factory.hide_courses = {"364893"}
        fake_fetcher_factory.undecidable_courses = {"364893"}
        assert self.run(tmp_path) == 1


class TestOnlyTheMissingCoursesAreAskedAbout:
    """查證要花學校的請求,不該多問一個。"""

    def test_a_round_with_no_removals_asks_nothing(self):
        fetcher = FakeFetcher()
        crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_courses=FIXTURE_COURSE_IDS,
        )
        assert fetcher.checked_courses == []

    def test_only_the_course_that_disappeared_is_asked_about(self):
        fetcher = FakeFetcher(hide_courses={"364893"}, gone_courses={"364893"})
        crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_courses=FIXTURE_COURSE_IDS,
        )
        assert fetcher.checked_courses == ["364893"]

    def test_without_a_baseline_nothing_is_asked(self):
        """第一次抓這個學期 —— 沒有基準就沒有停開要查證。"""
        fetcher = FakeFetcher(hide_courses={"364893"})
        crawl(fetcher, 115, 1, only_departments=["59"])
        assert fetcher.checked_courses == []

    def test_a_dept_scoped_run_gets_no_baseline_at_all(
        self, tmp_path, fake_fetcher_factory
    ):
        """--dept 只抓幾個系所,拿全校的索引來比會是「消失兩千多門」。

        跟 `_write_changes()` 對局部抓取略過變更紀錄是同一個理由。
        """
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--log-level", "CRITICAL"])

        fake_fetcher_factory.hide_courses = {"364893"}
        assert main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
                     "--dept", "59", "--log-level", "CRITICAL"]) == 0
        assert fake_fetcher_factory.created[-1].checked_courses == []


class TestTheVerificationHasABudget:
    """消失的課太多就抽樣,不逐筆問。

    抽樣而不是直接拒絕:真的大批停開會發生(README 記過一次 265 門的
    bulk_change),而「超過上限就一律不發布」對那種情況是**永久卡死** ——
    那些課不會再回來,每一輪都卡在同一個地方。
    """

    def test_a_mass_disappearance_only_asks_up_to_the_budget(self):
        fetcher = FakeFetcher(hide_courses=FIXTURE_COURSE_IDS)
        with pytest.raises(IncompleteCrawl):
            crawl(
                fetcher, 115, 1,
                only_departments=["59"],
                known_courses=FIXTURE_COURSE_IDS,
                removal_budget=2,
            )
        assert len(fetcher.checked_courses) == 2

    def test_a_sample_that_says_the_courses_are_still_there_rejects_the_round(self):
        """抓取缺口:整批都還在,抽到的那幾門就會現形。"""
        fetcher = FakeFetcher(hide_courses=FIXTURE_COURSE_IDS)
        with pytest.raises(IncompleteCrawl):
            crawl(
                fetcher, 115, 1,
                only_departments=["59"],
                known_courses=FIXTURE_COURSE_IDS,
                removal_budget=2,
            )

    def test_a_sample_that_confirms_them_gone_lets_the_round_through(self):
        """真的大批停開:抽樣整批「查無課號」,不該被卡住。"""
        fetcher = FakeFetcher(
            hide_courses=FIXTURE_COURSE_IDS, gone_courses=FIXTURE_COURSE_IDS
        )
        result = crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_courses=FIXTURE_COURSE_IDS,
            removal_budget=2,
        )
        assert result.courses == []
        assert len(fetcher.checked_courses) == 2

    def test_within_budget_it_asks_about_every_one(self):
        fetcher = FakeFetcher(hide_courses={"364893"}, gone_courses={"364893"})
        crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_courses=FIXTURE_COURSE_IDS,
            removal_budget=3,
        )
        assert fetcher.checked_courses == ["364893"]
