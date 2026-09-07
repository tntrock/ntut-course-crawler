"""教室容量:狀態檔、挑選規則、抓取迴圈、寫檔。

容量是教室主檔的屬性,與學期無關(實測 year=199 仍回傳正確容量)。
所以狀態檔以教室代碼為主鍵,不分學期;每月重抓一輪讓現值保持正確。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from crawler.http import SiteUnavailable
from crawler.main import (
    DEFAULT_CAPACITY_REFRESH_AFTER,
    classroom_targets,
    crawl_capacity,
    select_capacity_targets,
    crawl,
)
from crawler.output import read_capacity, write_capacity, write_outputs
from tests.conftest import load_fixture
from tests.test_main import FakeFetcher


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

    def test_corrupt_entry_does_not_crash(self):
        """壞掉的狀態檔(非 dict 的垃圾值)不該讓挑選流程崩潰。

        這是抓取路徑上**第一個**會碰到 known.get(code) 的地方 ——
        跑在 crawl_capacity()、_write_classrooms() 之前。同一份壞掉的
        capacity.json(垃圾字串/垃圾陣列)不該在這裡就用 AttributeError
        炸掉整個抓取,連一間教室都還沒抓到。
        """
        known = {"48": "garbage_string", "9": ["garbage_list"]}
        # 讀不出形狀就視為需要重抓(跟其他「讀不懂就重抓」的規則一致)
        assert set(select_capacity_targets(known, self.targets())) == {"48", "9"}

    def test_naive_checked_at_does_not_crash(self):
        """`checked_at` 沒有時區資訊(缺 Z)不該讓 `now - stamp` 炸掉。

        跟 output.py 的 read_semester_times() 同一個慣例:naive 的
        datetime 當作 UTC 處理。
        """
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        known = {
            "48": {"capacity": 50, "checked_at": "2026-09-06T00:00:00"},  # 沒有 Z
        }
        # "48" 只過了 24 小時,還沒到預設的 480 小時門檻,不該被選中;
        # 重點是這一行不會拋 TypeError。
        assert select_capacity_targets(known, self.targets(), now=now) == ["9"]


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

    def test_unsafe_code_is_skipped_not_used_in_a_url(self, tmp_path):
        """跟 output.py 的 _write_classrooms() 用同一個信任邊界:代碼要先
        驗過才能拿去組 URL。這裡原本沒有驗,壞掉的代碼會被直接接進去。
        """
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "../etc/passwd", "name": "不合法代碼"},
        ])
        fetcher = FakeCroomFetcher()
        stats = crawl_capacity(fetcher, tmp_path)
        assert stats["failed"] == 1
        assert stats["fetched"] == 0
        assert fetcher.urls == [], "不合法的代碼不該被拿去組 URL 發請求"
        errors = read(tmp_path / "errors.json")["errors"]
        assert any(e["classroom_id"] == "../etc/passwd" for e in errors)


class FakeCroomFetcherUnavailable:
    """模擬斷路器跳開:遇到 `trip_on` 那個代碼直接拋 `SiteUnavailable`。"""

    def __init__(self, trip_on):
        self.trip_on = trip_on
        self.urls: list[str] = []
        self.delay = 1.0

    def fetch(self, url, *, params=None):
        self.urls.append(url)
        if f"code={self.trip_on}" in url:
            raise SiteUnavailable("模擬:斷路器跳開")
        return load_fixture("croom_page_real.html")


class TestCrawlCapacitySiteUnavailable:
    """`SiteUnavailable` 代表學校端整批不可用,跟單一教室的版面問題不一樣。

    修好之前,crawl_capacity() 的 bare `except Exception` 把它當成普通
    失敗繼續打下一間 —— 斷路器已經跳開,剩下的請求只是白白被 Fetcher
    立刻拒絕,而且全部記成 failed,main() 沒有機制看出「這是整批失敗」。
    """

    def prepare(self, tmp_path):
        # 三間教室,代碼字母序是 48 < 7 < 9,所以 "48" 會第一個被選中。
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "48", "name": "三教307(e)"},
            {"id": "9", "name": "共同301"},
            {"id": "7", "name": "另一間"},
        ])

    def test_stops_the_batch_instead_of_hammering_every_room(self, tmp_path):
        self.prepare(tmp_path)
        fetcher = FakeCroomFetcherUnavailable(trip_on="48")
        stats = crawl_capacity(fetcher, tmp_path)
        assert stats["fetched"] == 0
        assert stats["failed"] == 1
        assert len(fetcher.urls) == 1, "斷路器跳開後不該再繼續打其他教室"

    def test_the_trip_is_still_recorded_as_an_error(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcherUnavailable(trip_on="48"), tmp_path)
        errors = read(tmp_path / "errors.json")["errors"]
        assert any(
            e["stage"] == "classroom" and e["classroom_id"] == "48" for e in errors
        )


class FakeUnparsableCroomFetcher:
    """回傳一個有資料列、但容量欄位不是數字的教室頁 ——「頁面抓得到
    但解析不出容量」那條分支,跟整個抓取拋例外的失敗路徑是兩回事。
    """

    def __init__(self):
        self.urls: list[str] = []
        self.delay = 1.0

    def fetch(self, url, *, params=None):
        self.urls.append(url)
        return (
            "<html><body><table>"
            "<tr><th>簡稱</th><th>全名</th><th>容量</th></tr>"
            "<tr><td>三教307(e)</td><td>第三教學大樓307室</td><td>--</td></tr>"
            "</table></body></html>"
        )


class TestCrawlCapacityValueTransitions:
    """spec §測試 item 2:值沒變時只更新 checked_at;值變了就覆蓋
    capacity;兩種情況都不新增欄位。外加「抓得到但解析不出容量」那條
    分支 —— 這四種情況原本一個都沒有測試涵蓋。
    """

    def prepare(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "48", "name": "三教307(e)"},
        ])

    def test_unchanged_value_only_updates_checked_at(self, tmp_path):
        self.prepare(tmp_path)
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2020-01-01T00:00:00Z"},
        })
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path)  # fixture 容量是 50
        assert stats["changed"] == 0
        entry = read_capacity(tmp_path)["48"]
        assert entry["capacity"] == 50
        assert entry["checked_at"] != "2020-01-01T00:00:00Z"

    def test_changed_value_overwrites_capacity_and_is_counted(self, tmp_path):
        self.prepare(tmp_path)
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 999, "checked_at": "2020-01-01T00:00:00Z"},
        })
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path)  # fixture 容量是 50
        assert stats["changed"] == 1
        assert read_capacity(tmp_path)["48"]["capacity"] == 50

    def test_every_entry_has_exactly_four_keys(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcher(), tmp_path)
        entry = read_capacity(tmp_path)["48"]
        assert set(entry) == {"name", "full_name", "capacity", "checked_at"}

    def test_page_fetched_but_capacity_unparseable(self, tmp_path):
        """釘住現行 spec'd 行為:解析不出容量時寫 `capacity: None`
        (覆蓋掉先前已知的好值)、`checked_at` 照樣更新、並記一筆錯誤。

        這是 spec 錯誤處理段明訂的行為,**不是**這次修正的範圍 —— 要不要
        改成「解析失敗時保留舊值」是使用者的決定(見 ledger Task 4 的
        deferred minor)。這個測試只是把現況釘住,行為一旦被改掉就會失敗。
        """
        self.prepare(tmp_path)
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2020-01-01T00:00:00Z"},
        })
        crawl_capacity(FakeUnparsableCroomFetcher(), tmp_path)
        entry = read_capacity(tmp_path)["48"]
        assert entry["capacity"] is None, "現行行為:讀不出來就覆蓋成 None"
        assert entry["checked_at"] != "2020-01-01T00:00:00Z"
        errors = read(tmp_path / "errors.json")["errors"]
        assert any(
            e["classroom_id"] == "48" and "讀不出容量" in e["error"] for e in errors
        )


class TestClassroomsGetCapacity:
    def result(self):
        return crawl(FakeFetcher(), 115, 1, only_departments=["59"])

    def test_capacity_is_stamped_from_the_state_file(self, tmp_path):
        # "434" 是 FakeFetcher 的測試資料裡真的存在的教室代碼
        # (資工系那批課程頁裡有一間教室代碼 434)—— 用不存在的 "48" 的話,
        # 每一筆 capacity 都會是 None,`all("capacity" in e for e in entries)`
        # 這種斷言連硬寫死 cap_value = None 的假實作都會通過,完全測不到
        # 「已知容量真的落地了」這件事。
        write_capacity(tmp_path, {
            "434": {"name": "測試教室", "full_name": "測試教室全名",
                    "capacity": 50, "checked_at": "2026-09-07T02:00:00Z"},
        })
        write_outputs(self.result(), tmp_path)
        entries = read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        assert all("capacity" in e for e in entries), "每一筆都要有這個欄位"
        by_id = {e["id"]: e for e in entries}
        assert "434" in by_id, "測試資料裡應該要有代碼 434 的教室"
        assert by_id["434"]["capacity"] == 50, "已知容量要真的落到 classrooms.json"

    def test_unknown_classrooms_get_null_not_zero(self, tmp_path):
        write_outputs(self.result(), tmp_path)   # 沒有狀態檔
        entries = read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        assert all(e["capacity"] is None for e in entries)

    def test_writing_does_not_fetch_anything(self, tmp_path):
        """補欄位只是查表,不該發任何請求 —— no_real_network 會抓到違規。"""
        write_capacity(tmp_path, {"48": {"name": "A", "full_name": "AA",
                                         "capacity": 50,
                                         "checked_at": "2026-09-07T02:00:00Z"}})
        write_outputs(self.result(), tmp_path)   # 不拋例外就算過

    def test_corrupt_capacity_entry_does_not_crash(self, tmp_path):
        """壞掉的狀態檔(非 dict 的垃圾值)不該讓整批寫檔無法啟動 —— 最壞就填 null。"""
        # 直接寫 capacity.json 並存入非 dict 的值(字串)。
        # "434" 是測試資料中實際出現的教室代碼。
        (tmp_path / "capacity.json").write_text(
            json.dumps({
                "schema_version": 3,
                "classroom_count": 2,
                "classrooms": {"434": "garbage_string", "37": ["garbage_list"]}  # 非 dict 的值
            }),
            encoding="utf-8"
        )
        # 不拋例外就算過 —— old code with `or {}` would crash with AttributeError
        write_outputs(self.result(), tmp_path)
        entries = read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        # 壞掉的項目應該填 None,不是拋例外
        assert all(e["capacity"] is None for e in entries)


class TestEndpointsListed:
    def test_capacity_is_advertised_in_meta(self, tmp_path):
        """meta.json 的 endpoints 是使用者發現新資料的唯一途徑。"""
        write_outputs(crawl(FakeFetcher(), 115, 1, only_departments=["59"]), tmp_path)
        paths = [e["path"] for e in read(tmp_path / "meta.json")["endpoints"]]
        assert "capacity.json" in paths
