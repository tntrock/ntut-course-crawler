"""教室容量:狀態檔、挑選規則、抓取迴圈、寫檔。

容量是教室主檔的屬性,與學期無關(實測 year=199 仍回傳正確容量)。
所以狀態檔以教室代碼為主鍵,不分學期;每月重抓一輪讓現值保持正確。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from crawler.main import (
    DEFAULT_CAPACITY_REFRESH_AFTER,
    classroom_targets,
    crawl_capacity,
    select_capacity_targets,
)
from crawler.output import read_capacity, write_capacity
from tests.conftest import load_fixture


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_semester_classrooms(out_dir, semester, entries):
    """造出一個學期的 classrooms.json(只放測試需要的欄位)。"""
    d = out_dir / semester
    d.mkdir(parents=True, exist_ok=True)
    (d / "classrooms.json").write_text(
        json.dumps({"schema_version": 3, "classrooms": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestCapacityState:
    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert read_capacity(tmp_path) == {}

    def test_round_trip(self, tmp_path):
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2026-09-07T02:00:00Z"},
        })
        assert read_capacity(tmp_path)["48"]["capacity"] == 50

    def test_envelope_has_count_and_schema(self, tmp_path):
        write_capacity(tmp_path, {
            "48": {"name": "A", "full_name": "AA", "capacity": 50,
                   "checked_at": "2026-09-07T02:00:00Z"},
            "9": {"name": "B", "full_name": "BB", "capacity": None,
                  "checked_at": "2026-09-07T02:00:00Z"},
        })
        payload = read(tmp_path / "capacity.json")
        assert payload["schema_version"] == 3
        assert payload["classroom_count"] == 2
        assert payload["generated_at"].endswith("Z")

    def test_unreadable_capacity_stays_null_not_zero(self, tmp_path):
        write_capacity(tmp_path, {
            "9": {"name": "B", "full_name": "BB", "capacity": None,
                  "checked_at": "2026-09-07T02:00:00Z"},
        })
        assert read_capacity(tmp_path)["9"]["capacity"] is None

    def test_corrupt_file_is_empty_not_an_exception(self, tmp_path):
        """壞掉的狀態檔不該讓整批抓取無法啟動 —— 最壞就是重抓一輪。"""
        (tmp_path / "capacity.json").write_text("{壞掉", encoding="utf-8")
        assert read_capacity(tmp_path) == {}


class TestClassroomTargets:
    def test_collects_codes_from_every_semester(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [{"id": "48", "name": "三教307"}])
        write_semester_classrooms(tmp_path, "110-1", [{"id": "9", "name": "共同301"}])
        targets = classroom_targets(tmp_path)
        assert set(targets) == {"48", "9"}

    def test_uses_the_newest_semester_a_code_appears_in(self, tmp_path):
        """用最新出現過的學期去抓 —— 那個頁面確定存在。"""
        write_semester_classrooms(tmp_path, "110-1", [{"id": "48", "name": "三教307"}])
        write_semester_classrooms(tmp_path, "115-1", [{"id": "48", "name": "三教307"}])
        assert classroom_targets(tmp_path)["48"] == ("三教307", 115, 1)

    def test_entries_without_a_code_are_skipped(self, tmp_path):
        """沒有代碼就組不出 URL,抓不了。"""
        write_semester_classrooms(tmp_path, "115-1", [{"id": None, "name": "未知"}])
        assert classroom_targets(tmp_path) == {}


class TestSelectCapacityTargets:
    def targets(self):
        return {"48": ("三教307", 115, 1), "9": ("共同301", 115, 1)}

    def test_unknown_codes_are_selected(self):
        assert set(select_capacity_targets({}, self.targets())) == {"48", "9"}

    def test_fresh_entries_are_skipped(self):
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        known = {
            "48": {"capacity": 50, "checked_at": "2026-09-06T00:00:00Z"},
            "9": {"capacity": 30, "checked_at": "2026-09-06T00:00:00Z"},
        }
        assert select_capacity_targets(known, self.targets(), now=now) == []

    def test_stale_entries_are_refetched(self):
        """容量會因改建而變,凍結的話新值永遠抓不到。"""
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        old = (now - timedelta(hours=DEFAULT_CAPACITY_REFRESH_AFTER + 1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        known = {"48": {"capacity": 50, "checked_at": old}}
        assert select_capacity_targets(known, self.targets(), now=now) == ["48", "9"]

    def test_limit_splits_the_work(self):
        picked = select_capacity_targets({}, self.targets(), limit=1)
        assert len(picked) == 1

    def test_unparsable_checked_at_is_treated_as_stale(self):
        """時間讀不懂就當作該重抓 —— 最壞是多抓一次,不會漏抓。"""
        known = {"48": {"capacity": 50, "checked_at": "壞掉"}}
        assert "48" in select_capacity_targets(known, self.targets())


class FakeCroomFetcher:
    """依 code 回傳教室頁。`fail_on` 裡的代碼會拋例外。"""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.urls = []
        self.delay = 1.0

    def fetch(self, url, *, params=None):
        self.urls.append(url)
        for code in self.fail_on:
            if f"code={code}" in url:
                raise RuntimeError(f"模擬 {code} 抓取失敗")
        return load_fixture("croom_page_real.html")


class TestCrawlCapacity:
    def prepare(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "48", "name": "三教307(e)"},
            {"id": "9", "name": "共同301"},
        ])

    def test_writes_capacity_for_every_classroom(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path)
        assert stats["fetched"] == 2
        state = read_capacity(tmp_path)
        assert state["48"]["capacity"] == 50
        assert state["48"]["checked_at"].endswith("Z")

    def test_second_run_skips_fresh_entries(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcher(), tmp_path)
        fetcher = FakeCroomFetcher()
        stats = crawl_capacity(fetcher, tmp_path)
        assert stats["fetched"] == 0
        assert fetcher.urls == [], "沒有到期就不該再打學校"

    def test_a_failure_does_not_stop_the_batch(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(fail_on={"48"}), tmp_path)
        assert stats["failed"] == 1
        assert stats["fetched"] == 1
        assert "9" in read_capacity(tmp_path)
        assert "48" not in read_capacity(tmp_path), "失敗的不進狀態檔,下次會重試"

    def test_failure_is_recorded_in_errors_json(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcher(fail_on={"48"}), tmp_path)
        errors = read(tmp_path / "errors.json")["errors"]
        matches = [e for e in errors if e["stage"] == "classroom" and e["classroom_id"] == "48"]
        assert matches
        # 一定要帶 year/sem,不然 write_errors() 的保留邏輯會把它當成沒有
        # 標學年期的舊格式殘留,在下一次任何學期的抓取時整筆消失。
        assert matches[0]["year"] == 115
        assert matches[0]["sem"] == 1

    def test_limit_splits_the_work(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path, limit=1)
        assert stats["fetched"] == 1
