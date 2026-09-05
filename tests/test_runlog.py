"""執行紀錄:runs.json 與爬蟲的側寫檔。

這個檔存在的理由是「狀態頁要看得到爬蟲現在如何」。所以驗的重點是:
失敗與被砍的那幾次也要留下紀錄,而且留下的東西要誠實 ——
沒有細節就明講沒有,不要留一堆 0 讓人誤以為跑了但什麼都沒抓到。
"""

from __future__ import annotations

import json

import pytest

from crawler.main import main as crawl_main
from crawler.runlog import RUN_LOG_LIMIT, append_run, build_record
from crawler.runlog import main as runlog_main
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def record(**kwargs):
    base = dict(
        status="success",
        workflow="crawl",
        run_id="1",
        attempt="1",
        event="schedule",
        repository="tntrock/ntut-course-crawler",
        server_url="https://github.com",
        summary=None,
    )
    base.update(kwargs)
    return build_record(**base)


class TestBuildRecord:
    def test_a_run_without_a_summary_says_so(self):
        """爬蟲被砍在寫出側寫檔之前。留 0 會被讀成「跑了但沒抓到東西」。"""
        r = record()
        assert r["detail"] is False
        assert "semesters" not in r
        assert r["status"] == "success"

    def test_the_status_comes_from_the_job_not_the_crawler(self):
        assert record(status="cancelled")["status"] == "cancelled"
        assert record(status="FAILURE")["status"] == "failure"
        assert record(status="")["status"] == "unknown"

    def test_it_links_back_to_the_actions_run(self):
        assert record()["url"] == (
            "https://github.com/tntrock/ntut-course-crawler/actions/runs/1"
        )

    def test_no_run_id_means_no_link(self):
        assert "url" not in record(run_id="")

    def test_a_summary_is_carried_through(self):
        r = record(
            summary={
                "started_at": "2026-09-05T00:00:00Z",
                "requests": 355,
                "cache_hits": 2,
                "semesters": [{"semester": "115-1", "courses": 2717}],
                "failed_semesters": ["114-2"],
                "exit_code": 0,
            }
        )
        assert r["detail"] is True
        assert r["requests"] == 355
        assert r["semesters"][0]["semester"] == "115-1"
        assert r["failed_semesters"] == ["114-2"]
        assert r["exit_code"] == 0


class TestAppendRun:
    def test_newest_first(self, tmp_path):
        append_run(tmp_path, record(run_id="1"))
        append_run(tmp_path, record(run_id="2"))
        data = read(tmp_path / "runs.json")
        assert [r["run_id"] for r in data["runs"]] == ["2", "1"]
        assert data["run_count"] == 2

    def test_old_records_fall_off_the_end(self, tmp_path):
        for n in range(RUN_LOG_LIMIT + 5):
            append_run(tmp_path, record(run_id=str(n)))
        data = read(tmp_path / "runs.json")
        assert len(data["runs"]) == RUN_LOG_LIMIT
        assert data["runs"][0]["run_id"] == str(RUN_LOG_LIMIT + 4)

    def test_a_retry_replaces_its_own_entry(self, tmp_path):
        """同一次 run 的同一次 attempt 只該有一筆,不要疊。"""
        append_run(tmp_path, record(run_id="7", attempt="1", status="failure"))
        append_run(tmp_path, record(run_id="7", attempt="1", status="success"))
        runs = read(tmp_path / "runs.json")["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "success"

    def test_a_second_attempt_is_its_own_entry(self, tmp_path):
        append_run(tmp_path, record(run_id="7", attempt="1", status="failure"))
        append_run(tmp_path, record(run_id="7", attempt="2", status="success"))
        assert len(read(tmp_path / "runs.json")["runs"]) == 2

    def test_a_broken_existing_file_is_rebuilt(self, tmp_path):
        (tmp_path / "runs.json").write_text("{壞掉的 JSON", encoding="utf-8")
        append_run(tmp_path, record())
        assert len(read(tmp_path / "runs.json")["runs"]) == 1


class TestRunlogCli:
    def test_it_records_without_a_summary_file(self, tmp_path):
        code = runlog_main(
            ["--out", str(tmp_path), "--status", "cancelled", "--run-id", "9"]
        )
        assert code == 0
        run = read(tmp_path / "runs.json")["runs"][0]
        assert run["status"] == "cancelled"
        assert run["detail"] is False

    def test_an_unreadable_summary_does_not_stop_the_record(self, tmp_path):
        bad = tmp_path / "summary.json"
        bad.write_text("{不是 JSON", encoding="utf-8")
        runlog_main(
            ["--out", str(tmp_path), "--status", "failure", "--summary", str(bad)]
        )
        assert read(tmp_path / "runs.json")["runs"][0]["detail"] is False


class TestCrawlerWritesItsSummary:
    def test_the_summary_describes_what_was_crawled(
        self, tmp_path, fake_fetcher_factory
    ):
        summary = tmp_path / "summary.json"
        code = crawl_main([
            "--year", "115", "--sem", "1", "--out", str(tmp_path / "data"),
            "--run-summary", str(summary), "--log-level", "CRITICAL",
        ])
        assert code == 0

        data = read(summary)
        assert data["exit_code"] == 0
        assert data["requests"] > 0
        assert [s["semester"] for s in data["semesters"]] == ["115-1"]
        assert data["semesters"][0]["courses"] > 0
        assert data["failed_semesters"] == []

    def test_a_failed_semester_shows_up_in_the_summary(
        self, tmp_path, fake_fetcher_factory
    ):
        """整個學期抓不到時,側寫檔要記下來 —— 那正是狀態頁要顯示的東西。"""
        summary = tmp_path / "summary.json"
        crawl_main([
            "--years", "114", "--out", str(tmp_path / "data"),
            "--run-summary", str(summary), "--log-level", "CRITICAL",
        ])
        data = read(summary)
        assert data["semesters"] or data["failed_semesters"]

    def test_the_summary_survives_to_be_read_by_runlog(
        self, tmp_path, fake_fetcher_factory
    ):
        """兩支合起來用:爬蟲寫側寫檔,runlog 把它併進 runs.json。"""
        out = tmp_path / "data"
        summary = tmp_path / "summary.json"
        crawl_main([
            "--year", "115", "--sem", "1", "--out", str(out),
            "--run-summary", str(summary), "--log-level", "CRITICAL",
        ])
        runlog_main([
            "--out", str(out), "--status", "success",
            "--summary", str(summary), "--run-id", "42", "--workflow", "crawl",
        ])

        run = read(out / "runs.json")["runs"][0]
        assert run["detail"] is True
        assert run["workflow"] == "crawl"
        assert run["semesters"][0]["semester"] == "115-1"
        assert run["exit_code"] == 0

    def test_no_flag_means_no_file(self, tmp_path, fake_fetcher_factory):
        crawl_main([
            "--year", "115", "--sem", "1", "--out", str(tmp_path / "data"),
            "--log-level", "CRITICAL",
        ])
        assert not list(tmp_path.glob("*.json"))
