"""Phase 6:教學大綱的解析、分批抓取與輸出。

大綱是**一門課一頁**,全校一輪 2,700 多頁。這裡驗的重點是分批的邏輯 ——
沒抓過的優先、每一門最終都輪得到、中途失敗不賠掉已抓好的。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crawler.main import crawl, crawl_syllabi, main, select_syllabus_targets
from crawler.models import Course
from crawler.output import read_syllabus_state
from crawler.parse_syllabus import parse_syllabus
# fake_fetcher_factory 讓 main() 也用假的 Fetcher —— 測試一律不連網
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
class TestParseSyllabus:
    @pytest.fixture
    def parsed(self, fixture):
        return parse_syllabus(fixture("syllabus_page_real.html"))

    def test_teacher_name_drops_the_office_hours_link(self, parsed):
        """同一格裡還有一個「教師諮商時間」的連結,不能混進姓名。"""
        assert parsed["teacher_name"] == "白敦文"

    def test_email_drops_the_mailto_icon(self, parsed):
        assert parsed["teacher_email"] == "twp@ntut.edu.tw"

    def test_updated_at_is_converted_to_utc(self, parsed):
        """學校顯示台灣時間又沒標時區,全站其他時間戳都是 UTC,統一過去。"""
        assert parsed["updated_at"] == "2026-08-11T01:00:23Z"

    def test_long_text_fields_survive_intact(self, parsed):
        assert "Digital image fundamentals" in parsed["outline"]
        assert parsed["schedule"].startswith("1. Introduce to Image Processing")
        assert parsed["assessment"] == "Quiz:15%, Homework:15%, Midterm 35%, Final 35%."

    def test_bulleted_fields_become_lists(self, parsed):
        assert parsed["sdgs"] == [
            "SDG4：優質教育（Quality Education）",
            "SDG9：產業創新與基礎設施（Industry, Innovation and Infrastructure）",
            "SDG11：永續城市與社區（Sustainable Cities and Communities）",
        ]
        assert len(parsed["ai_usage"]) == 3
        assert all(not v.startswith("●") for v in parsed["ai_usage"])

    def test_flexible_learning_is_a_nested_table(self, parsed):
        flex = parsed["flexible_learning"]
        assert flex["hours"] == 3
        assert len(flex["category"]) == 3
        assert "image processing" in flex["content"]
        assert flex["assessment_ratio"]

    def test_a_page_without_a_syllabus_returns_empty(self):
        """有些課老師根本沒填。那不是錯誤,回空 dict 讓呼叫端自己決定。"""
        assert parse_syllabus("<html><body>查無資料</body></html>") == {}

    def test_unknown_labels_are_kept_in_extra(self, fixture):
        """學校加新欄位時不能靜靜漏掉 —— SDGs 與 AI 顯然就是近年才加的。"""
        html = fixture("syllabus_page_real.html").replace(
            "<th>備註", "<th>某個全新的欄位"
        )
        parsed = parse_syllabus(html)
        assert "某個全新的欄位" in parsed.get("extra", {})
        assert "notes" not in parsed


# --------------------------------------------------------------------------
# 分批
# --------------------------------------------------------------------------
class TestSelectTargets:
    def course(self, cid, url="https://aps.ntut.edu.tw/course/tw/ShowSyllabus.jsp?snum=1"):
        return Course(id=cid, name_zh=f"課{cid}", syllabus_url=url)

    def pick(self, courses, fetched, limit=None, refresh_after=720.0):
        return [
            c.id
            for c in select_syllabus_targets(
                courses, fetched, limit=limit, refresh_after=refresh_after, now=NOW
            )
        ]

    def test_courses_without_a_syllabus_link_are_skipped(self):
        """跨校選課那類課程沒有大綱連結,硬打一頁只是浪費。"""
        courses = [self.course("1"), self.course("2", url=None)]
        assert self.pick(courses, {}) == ["1"]

    def test_never_fetched_comes_first(self):
        courses = [self.course("1"), self.course("2")]
        assert self.pick(courses, {"1": stamp(10_000)}) == ["2", "1"]

    def test_recently_fetched_are_skipped(self):
        courses = [self.course("1"), self.course("2")]
        fetched = {"1": stamp(1), "2": stamp(10_000)}
        assert self.pick(courses, fetched) == ["2"]

    def test_oldest_first_so_everything_eventually_gets_a_turn(self):
        """分批的前提是排序公平,不能有課永遠排不到。"""
        courses = [self.course(str(n)) for n in range(1, 5)]
        fetched = {"1": stamp(800), "2": stamp(5000), "3": stamp(900)}
        assert self.pick(courses, fetched) == ["4", "2", "3", "1"]

    def test_limit_splits_the_work_into_batches(self):
        courses = [self.course(str(n)) for n in range(1, 6)]
        assert self.pick(courses, {}, limit=2) == ["1", "2"]

    def test_an_unreadable_timestamp_is_treated_as_never_fetched(self):
        assert self.pick([self.course("1")], {"1": "壞掉的時間"}) == ["1"]


# --------------------------------------------------------------------------
# 抓取與輸出
# --------------------------------------------------------------------------
class TestCrawlSyllabi:
    @pytest.fixture
    def result(self):
        r = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
        r.partial = False
        return r

    def test_writes_one_file_per_course(self, tmp_path, result):
        fetcher = FakeFetcher()
        n = crawl_syllabi(
            fetcher, result, tmp_path, limit=None, refresh_after=720.0, pretty=True
        )
        with_url = [c for c in result.courses if c.syllabus_url]
        assert n == len(with_url)
        for course in with_url:
            data = read(tmp_path / "115-1" / "syllabus" / f"{course.id}.json")
            assert data["course_id"] == course.id
            assert data["course_name"] == course.name_zh
            assert data["has_content"] is True
            assert data["teacher_name"] == "白敦文"
            assert data["url"] == course.syllabus_url

    def test_the_index_records_what_was_fetched(self, tmp_path, result):
        crawl_syllabi(FakeFetcher(), result, tmp_path, limit=None, refresh_after=720.0)
        state = read_syllabus_state(tmp_path)["115-1"]
        assert set(state) == {c.id for c in result.courses if c.syllabus_url}

        index = read(tmp_path / "syllabus.json")
        entry = index["semesters"][0]
        assert entry["semester"] == "115-1"
        assert entry["fetched"] == len(state)
        assert entry["course_count"] == len(result.courses)
        assert entry["with_url"] == len(state)

    def test_a_second_run_fetches_nothing(self, tmp_path, result):
        crawl_syllabi(FakeFetcher(), result, tmp_path, limit=None, refresh_after=720.0)
        fetcher = FakeFetcher()
        assert (
            crawl_syllabi(fetcher, result, tmp_path, limit=None, refresh_after=720.0)
            == 0
        )
        assert not [c for c in fetcher.calls if c]

    def test_batches_resume_where_they_left_off(self, tmp_path, result):
        """一批 800 頁、幾天輪完一圈 —— 每一批都要接著上一批繼續。"""
        with_url = [c.id for c in result.courses if c.syllabus_url]
        first = crawl_syllabi(FakeFetcher(), result, tmp_path, limit=2, refresh_after=720.0)
        assert first == 2
        done = set(read_syllabus_state(tmp_path)["115-1"])
        assert len(done) == 2

        crawl_syllabi(FakeFetcher(), result, tmp_path, limit=2, refresh_after=720.0)
        assert set(read_syllabus_state(tmp_path)["115-1"]) > done
        assert len(read_syllabus_state(tmp_path)["115-1"]) == min(4, len(with_url))

    def test_a_course_without_a_syllabus_is_still_recorded(self, tmp_path, result):
        """老師沒填也要記時間戳,不然每次執行都會再問一次同一頁。"""
        fetcher = FakeFetcher()
        target = next(c for c in result.courses if c.syllabus_url)
        fetcher.no_syllabus = {target.syllabus_url}
        crawl_syllabi(fetcher, result, tmp_path, limit=None, refresh_after=720.0)

        data = read(tmp_path / "115-1" / "syllabus" / f"{target.id}.json")
        assert data["has_content"] is False
        assert "outline" not in data
        assert target.id in read_syllabus_state(tmp_path)["115-1"]

    def test_a_failure_keeps_what_was_already_fetched(self, tmp_path, result):
        """一批十幾分鐘,中途失敗不該賠掉前面抓好的。"""
        fetcher = FakeFetcher(fail_on=set())
        calls = {"n": 0}
        real = fetcher.fetch

        def flaky(url, *, params=None):
            calls["n"] += 1
            if "ShowSyllabus" in url and calls["n"] > 2:
                raise RuntimeError("模擬大綱抓取失敗")
            return real(url, params=params)

        fetcher.fetch = flaky
        crawl_syllabi(fetcher, result, tmp_path, limit=None, refresh_after=720.0)

        assert read_syllabus_state(tmp_path)["115-1"], "已抓好的要留著"
        assert any(e["stage"] == "syllabus" for e in result.errors)

    def test_meta_advertises_the_endpoints(self, tmp_path, result):
        from crawler.output import write_outputs

        write_outputs(result, tmp_path)
        paths = {e["path"] for e in read(tmp_path / "meta.json")["endpoints"]}
        assert "syllabus.json" in paths
        assert "{semester}/syllabus/{course_id}.json" in paths


class TestSyllabusCli:
    def test_off_by_default(self, tmp_path, fake_fetcher_factory):
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--log-level", "CRITICAL"])
        assert not (tmp_path / "syllabus.json").exists()

    def test_with_syllabus_writes_the_files(self, tmp_path, fake_fetcher_factory):
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--with-syllabus", "--log-level", "CRITICAL"])
        assert (tmp_path / "syllabus.json").is_file()
        assert list((tmp_path / "115-1" / "syllabus").glob("*.json"))

    def test_dept_smoke_tests_never_fetch_syllabi(self, tmp_path, fake_fetcher_factory):
        """--dept 是局部抓取,大綱的進度紀錄會變成半套。"""
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path), "--dept", "59",
              "--with-syllabus", "--log-level", "CRITICAL"])
        assert not (tmp_path / "syllabus.json").exists()
