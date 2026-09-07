"""教室容量:狀態檔、挑選規則、抓取迴圈、寫檔。

容量是教室主檔的屬性,與學期無關(實測 year=199 仍回傳正確容量)。
所以狀態檔以教室代碼為主鍵,不分學期;每月重抓一輪讓現值保持正確。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from crawler.http import SiteUnavailable
from crawler.main import (
    DEFAULT_CAPACITY_REFRESH_AFTER,
    classroom_targets,
    crawl_capacity,
    main,
    select_capacity_targets,
    crawl,
)
from crawler.output import read_capacity, write_capacity, write_outputs
from tests.conftest import load_fixture
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401


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


class FakeCroomFetcherNoCapacity:
    """回傳「學校沒登記座位數」的教室頁:名稱讀得到,容量那格是空的。

    這不是解析失敗 —— 線上 445 間裡有 229 間就長這樣(一教101、綜科319…,
    連旁邊的使用率欄位也一起空)。實測打 115-1 / 110-1 / 105-1 / 199-1
    四個學年期都是空的,所以是學校主檔裡就沒這筆。
    """

    def __init__(self, blank=None):
        #: None 代表每一間都回空白;給集合則只有那些代碼回空白。
        self.blank = blank
        self.urls = []
        self.delay = 1.0

    def fetch(self, url, *, params=None):
        self.urls.append(url)
        if self.blank is None or any(f"code={code}" in url for code in self.blank):
            return load_fixture("croom_page_no_capacity.html")
        return load_fixture("croom_page_real.html")


class TestBlankCapacityIsNotAlwaysAnError:
    """`null` 有兩種完全不同的意思,混在一起就兩邊都廢掉。

    (a) 學校沒登記 —— 永久、正確、佔線上 229/445。
    (b) 原本有值、這次讀不到 —— 版面改了的災難信號。

    修好之前兩者都記一筆「頁面抓得到但讀不出容量」,於是第一次全量跑完
    errors.json 就變成 229 筆同樣的訊息(error_count 剛好 229),其他錯誤
    被整個擠掉,而真正該示警的 (b) 反而淹沒在裡面看不見。
    """

    def prepare(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "48", "name": "三教307(e)"},
            {"id": "9", "name": "共同301"},
        ])

    def errors(self, tmp_path):
        path = tmp_path / "errors.json"
        return read(path)["errors"] if path.exists() else []

    def test_a_room_that_never_had_a_value_is_not_an_error(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcherNoCapacity(), tmp_path)
        assert self.errors(tmp_path) == [], (
            "學校沒登記座位數是正常的,不該塞進 errors.json"
        )

    def test_a_room_that_never_had_a_value_still_records_null(self, tmp_path):
        """不記錯誤不等於不記資料 —— 使用端要看得出「查過了,學校沒登」。"""
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcherNoCapacity(), tmp_path)
        state = read_capacity(tmp_path)
        assert state["48"]["capacity"] is None
        assert state["48"]["checked_at"].endswith("Z")

    def seed(self, tmp_path, capacity=50):
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": capacity, "checked_at": "2020-01-01T00:00:00Z"},
        })

    def test_a_value_that_disappears_keeps_the_old_number(self, tmp_path):
        """座位數不會無聲消失。改建會改變數字,不會把數字變成空白。"""
        self.prepare(tmp_path)
        self.seed(tmp_path)
        crawl_capacity(FakeCroomFetcherNoCapacity(), tmp_path, refresh_after=0)
        assert read_capacity(tmp_path)["48"]["capacity"] == 50, (
            "原本有值卻被 null 蓋掉了 —— 學校改版就會讓 216 間同時歸零"
        )

    def test_a_value_that_disappears_is_an_error(self, tmp_path):
        self.prepare(tmp_path)
        self.seed(tmp_path)
        crawl_capacity(FakeCroomFetcherNoCapacity(), tmp_path, refresh_after=0)
        matches = [
            e for e in self.errors(tmp_path)
            if e.get("classroom_id") == "48"
        ]
        assert matches, "值消失了卻沒有任何紀錄,四小時後就查無對證"
        assert matches[0]["year"] == 115 and matches[0]["sem"] == 1

    def test_checked_at_still_moves_when_the_value_is_kept(self, tmp_path):
        """保留舊值,但要留下「這次確實查過」的痕跡,否則下次還會重排。"""
        self.prepare(tmp_path)
        self.seed(tmp_path)
        crawl_capacity(FakeCroomFetcherNoCapacity(), tmp_path, refresh_after=0)
        assert read_capacity(tmp_path)["48"]["checked_at"] != "2020-01-01T00:00:00Z"

    def test_stats_separate_the_two_kinds_of_blank(self, tmp_path):
        self.prepare(tmp_path)
        self.seed(tmp_path)   # 只有 48 原本有值,9 是全新的
        stats = crawl_capacity(
            FakeCroomFetcherNoCapacity(), tmp_path, refresh_after=0
        )
        assert stats["fetched"] == 2
        assert stats["vanished"] == 1, "只有 48 是「原本有值、現在沒了」"
        assert stats["had_value"] == 1, "分母:這次抓到、而且原本有值的教室數"


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
        """容量那格有東西、但不是數字(`--`)。原本有值就保留舊值 + 記錯誤。

        這條原本釘的是相反的行為(讀不出來就覆蓋成 None),那是 spec 明訂的。
        線上第一次全量跑之後改掉:445 間裡有 229 間學校根本沒登記座位數,
        於是 `null` 同時代表「學校沒登」和「解析失敗」,兩者無法區分 ——
        errors.json 被 229 筆同樣的訊息塞滿,而真正的災難信號反而看不見。
        分界改成「**原本有沒有值**」,那才是真的能區分兩者的東西。
        """
        self.prepare(tmp_path)
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2020-01-01T00:00:00Z"},
        })
        crawl_capacity(FakeUnparsableCroomFetcher(), tmp_path)
        entry = read_capacity(tmp_path)["48"]
        assert entry["capacity"] == 50, "原本有值就不該被讀不出來的結果蓋掉"
        assert entry["checked_at"] != "2020-01-01T00:00:00Z"
        errors = read(tmp_path / "errors.json")["errors"]
        assert any(
            e["classroom_id"] == "48" and "保留舊值" in e["error"] for e in errors
        )

    def test_unparseable_capacity_with_no_previous_value_writes_null(self, tmp_path):
        """沒有舊值可保留時就照實寫 null —— 「查過了,讀不到」也是資訊。"""
        self.prepare(tmp_path)
        crawl_capacity(FakeUnparsableCroomFetcher(), tmp_path)
        entry = read_capacity(tmp_path)["48"]
        assert entry["capacity"] is None
        assert entry["checked_at"].endswith("Z")


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


class TestClassroomsJsonCarriesThisRunsCapacity:
    """`--with-capacity` 跑完,各學期的 classrooms.json 要帶**這次**抓到的容量。

    修好之前 main() 的順序是反的:先 `write_outputs()`(裡面的
    `_write_classrooms()` 去讀 capacity.json)才 `crawl_capacity()`
    (寫 capacity.json)。於是學期檔永遠帶著上一輪的容量。

    第一次上線就踩到了:capacity.json 有 216 間的實際座位數,
    `115-1/classrooms.json` 卻只有 1 間有值 —— 因為那一輪讀到的是前一次
    測試跑的 5 間狀態檔。要等下一班 crawl 才補得回來。
    """

    def fake_capacity_crawl(self, tmp_path):
        """假裝容量抓取跑完並寫了狀態檔,回傳可餵給 monkeypatch 的函式。"""

        def run(fetcher, out_dir, **kwargs):
            write_capacity(Path(out_dir), {
                "434": {"name": "六教427(e)", "full_name": "第六教學大樓427室",
                        "capacity": 50, "checked_at": "2026-09-07T02:00:00Z"},
            })
            return {"fetched": 1, "changed": 0, "failed": 0}

        return run

    def test_capacity_lands_in_the_same_run(
        self, tmp_path, fake_fetcher_factory, monkeypatch
    ):
        monkeypatch.setattr(
            "crawler.main.crawl_capacity", self.fake_capacity_crawl(tmp_path)
        )
        main([
            "--year", "115", "--sem", "1", "--out", str(tmp_path),
            "--dept", "59", "--with-capacity", "--log-level", "CRITICAL",
        ])
        rooms = {
            r["id"]: r
            for r in read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        }
        assert "434" in rooms, "測試前提:假課表裡要有教室 434"
        assert rooms["434"]["capacity"] == 50, (
            "classrooms.json 沒帶到這次抓到的容量,又落後一輪了"
        )

    def test_without_the_flag_nothing_extra_is_written(
        self, tmp_path, fake_fetcher_factory, monkeypatch
    ):
        """沒開 `--with-capacity` 就不該有補寫這一步 —— 那是白做工。"""
        calls = []
        real = __import__(
            "crawler.output", fromlist=["write_classrooms"]
        ).write_classrooms
        monkeypatch.setattr(
            "crawler.main.write_classrooms",
            lambda *a, **k: (calls.append(a), real(*a, **k))[1],
        )
        main([
            "--year", "115", "--sem", "1", "--out", str(tmp_path),
            "--dept", "59", "--log-level", "CRITICAL",
        ])
        assert calls == []
