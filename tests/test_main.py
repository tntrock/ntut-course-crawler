"""Phase 4 驗收:抓取流程、去重合併、輸出結構、錯誤處理。

完全離線 —— 用假的 Fetcher 直接回傳 Phase 0 的 fixture。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crawler.config import SCHEMA_VERSION
from crawler.main import (
    CONSECUTIVE_FAILURE_LIMIT,
    backfill_semesters,
    crawl,
    discover_semesters,
    main,
    parse_year_range,
    select_semesters,
    write_outputs,
)
from crawler.http import SiteUnavailable
from crawler.models import Semester
from tests.conftest import load_fixture


class FakeFetcher:
    """依 format / code 回傳對應 fixture 的假抓取器。"""

    def __init__(
        self,
        *,
        fail_on: set[str] | None = None,
        fail_semesters: set[tuple[int, int]] | None = None,
        unavailable_after: int | None = None,
    ) -> None:
        self.fail_on = fail_on or set()
        # 整個學期抓不到(學校維護、連線逾時):總覽頁就先炸掉
        self.fail_semesters = fail_semesters or set()
        # 抓到第幾個請求時模擬斷路器跳開(None = 從頭到尾都正常)
        self.unavailable_after = unavailable_after
        self.unavailable = False
        self.calls: list[tuple[int, str | None]] = []
        self.request_count = 0
        self.cache_hit_count = 0
        self.delay = 1.0

    def fetch(self, url: str, *, params: dict | None = None) -> str:
        params = params or {}
        fmt = int(params.get("format", 0))
        code = params.get("code")
        self.calls.append((fmt, code))
        self.request_count += 1

        if self.unavailable:
            raise SiteUnavailable(f"模擬:站台已判定不可用 {url}")
        if (
            self.unavailable_after is not None
            and self.request_count >= self.unavailable_after
        ):
            self.unavailable = True
            raise SiteUnavailable(f"模擬:斷路器在第 {self.request_count} 個請求跳開")

        if code in self.fail_on:
            raise RuntimeError(f"模擬 {code} 抓取失敗")

        key = (params.get("year"), params.get("sem"))
        if key in self.fail_semesters:
            raise TimeoutError(f"模擬 {key[0]}-{key[1]} 連線逾時")

        if url == "course.jsp":
            # 首頁列出 115-1 與 114-2 兩個學年期
            return load_fixture("course_home_real.html")
        if fmt == -2:
            return load_fixture("subj_overview.html")
        if fmt == -3:
            return load_fixture("dept_page_real.html")
        if fmt == -4:
            return load_fixture("course_list_real.html")
        raise AssertionError(f"沒預期到的 format={fmt}")


@pytest.fixture
def result():
    """只抓資工系,5 個班級各回同一份 6 門課的課程頁。"""
    return crawl(FakeFetcher(), 115, 1, only_departments=["59"])


@pytest.fixture
def fake_fetcher_factory(monkeypatch):
    """讓 main() 也用假的 Fetcher。

    測試套件在任何情況下都不該連到學校伺服器 —— 沒有這個 fixture,
    測 CLI 就會變成每跑一次 pytest 就打對方一輪。
    """

    class Factory:
        def __init__(self) -> None:
            self.fail_on: set[str] = set()
            self.fail_semesters: set[tuple[int, int]] = set()
            self.unavailable_after: int | None = None
            self.created: list[FakeFetcher] = []

        def __call__(self, **kwargs):
            fetcher = FakeFetcher(
                fail_on=self.fail_on,
                fail_semesters=self.fail_semesters,
                unavailable_after=self.unavailable_after,
            )
            self.created.append(fetcher)
            return fetcher

    factory = Factory()
    monkeypatch.setattr("crawler.main.Fetcher", factory)
    return factory


class TestCrawlFlow:
    def test_follows_all_three_levels(self, result):
        fetcher = FakeFetcher()
        crawl(fetcher, 115, 1, only_departments=["59"])
        formats = [fmt for fmt, _ in fetcher.calls]
        assert formats == [-2, -3] + [-4] * 5

    def test_department_filter(self, result):
        assert [d.id for d in result.departments] == ["59"]
        assert result.ok_departments == 1
        assert result.failed_departments == 0

    def test_unknown_department_filter_warns(self, caplog):
        with caplog.at_level("WARNING"):
            r = crawl(FakeFetcher(), 115, 1, only_departments=["ZZ"])
        assert r.departments == []
        assert "不存在" in caplog.text

    def test_class_groups_recorded(self, result):
        assert [g.id for g in result.class_groups["59"]] == [
            "2915", "3032", "3138", "3718", "3743",
        ]


class TestDeduplication:
    def test_same_course_id_appears_once(self, result):
        """5 個班級頁各有同樣 6 門課 → 去重後仍是 6 門。"""
        assert len(result.courses) == 6
        assert len({c.id for c in result.courses}) == 6

    def test_all_class_ids_are_kept(self, result):
        """去重不能把「這門課開給哪些班」的資訊弄丟。"""
        course = next(c for c in result.courses if c.id == "364893")
        assert course.class_ids == ["2915", "3032", "3138", "3718", "3743"]

    def test_department_id_is_not_duplicated(self, result):
        course = next(c for c in result.courses if c.id == "364893")
        assert course.department_ids == ["59"]

    def test_class_names_are_deduplicated_by_value(self, result):
        """五個假班級頁的表頭都是「資工四」,合併後只該留一個。"""
        course = next(c for c in result.courses if c.id == "364893")
        assert course.classes == ["資工四"]

    def test_courses_are_sorted_by_id(self, result):
        ids = [c.id for c in result.courses]
        assert ids == sorted(ids)


class TestErrorHandling:
    def test_failed_department_is_recorded_and_crawl_continues(self):
        """單一系所失敗不能拖垮整批(plan.md §3 Phase 4)。"""
        r = crawl(
            FakeFetcher(fail_on={"59"}), 115, 1, only_departments=["59", "31"]
        )
        assert r.failed_departments == 1
        assert r.ok_departments == 1
        assert [e["department_id"] for e in r.errors] == ["59"]
        assert r.errors[0]["stage"] == "department"
        assert "模擬" in r.errors[0]["error"]
        assert len(r.courses) == 6  # 電機系那邊照樣抓到了

    def test_failed_class_group_does_not_kill_the_department(self):
        r = crawl(FakeFetcher(fail_on={"3138"}), 115, 1, only_departments=["59"])
        assert r.ok_departments == 1
        assert [e["stage"] for e in r.errors] == ["class_group"]
        assert r.errors[0]["class_group_id"] == "3138"
        assert len(r.courses) == 6

    def test_exit_code_is_zero_when_some_departments_succeed(
        self, tmp_path, fake_fetcher_factory
    ):
        code = main(
            ["--year", "115", "--sem", "1", "--out", str(tmp_path), "--dept", "59"]
        )
        assert code == 0
        assert (tmp_path / "115-1" / "courses" / "59.json").is_file()

    def test_exit_code_is_one_when_every_department_fails(
        self, tmp_path, fake_fetcher_factory
    ):
        fake_fetcher_factory.fail_on = {"59"}
        code = main(
            ["--year", "115", "--sem", "1", "--out", str(tmp_path), "--dept", "59"]
        )
        assert code == 1


class TestOutputFiles:
    @pytest.fixture
    def out(self, tmp_path, result):
        write_outputs(result, tmp_path, pretty=True)
        return tmp_path

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_directory_layout(self, out):
        assert (out / "meta.json").is_file()
        assert (out / "index.json").is_file()
        assert (out / "errors.json").is_file()
        assert (out / "115-1" / "departments.json").is_file()
        assert (out / "115-1" / "courses" / "59.json").is_file()

    def test_every_file_carries_the_schema_version(self, out):
        for path in out.rglob("*.json"):
            assert self.read(path)["schema_version"] == SCHEMA_VERSION, path

    def test_course_files_are_named_by_department_code(self, out):
        names = {p.name for p in (out / "115-1" / "courses").iterdir()}
        assert names == {"59.json"}

    def test_department_file_has_three_levels(self, out):
        data = self.read(out / "115-1" / "departments.json")
        dept = data["departments"][0]
        assert dept["college"] == "電資學院"      # 學院
        assert dept["id"] == "59"                 # 系所
        assert len(dept["class_groups"]) == 5     # 班級
        assert dept["course_count"] == 6

    def test_course_file_content(self, out):
        data = self.read(out / "115-1" / "courses" / "59.json")
        assert data["department"]["name"] == "資工系"
        course = next(c for c in data["courses"] if c["id"] == "364893")
        assert course["name_zh"] == "數位影像處理"
        assert course["time_slots"] == [
            {"day": 5, "day_name": "五", "periods": ["2", "3", "4"]}
        ]

    def test_index_is_lightweight(self, out):
        data = self.read(out / "index.json")
        assert data["course_count"] == 6
        entry = data["courses"][0]
        # 索引刻意不放完整課程物件。teacher_codes / class_ids 是為了讓搜尋結果
        # 能直接跳到 teachers/{code}.json 與 classes/{id}.json,少了就沒得篩。
        assert set(entry) == {
            "id", "name_zh", "teachers", "teacher_codes", "time_slots",
            "department_ids", "class_ids", "credits",
            "required", "requirement_type", "year", "sem",
        }
        assert "classrooms" not in entry and "syllabus_url" not in entry

    def test_meta_has_lookup_tables(self, out):
        meta = self.read(out / "meta.json")
        assert len(meta["periods"]) == 14
        assert len(meta["requirement_symbols"]) == 6
        assert meta["semesters"][0]["course_count"] == 6
        assert meta["semesters"][0]["path"] == "115-1"
        assert "source" in meta and "disclaimer" in meta

    def test_errors_file_is_written_even_when_empty(self, out):
        assert self.read(out / "errors.json")["errors"] == []

    def test_stale_errors_do_not_survive_a_clean_run(self, tmp_path, result):
        (tmp_path).mkdir(exist_ok=True)
        (tmp_path / "errors.json").write_text(
            json.dumps({"errors": [{"stage": "old"}]}), encoding="utf-8"
        )
        write_outputs(result, tmp_path)
        assert self.read(tmp_path / "errors.json")["errors"] == []

    def test_second_semester_merges_instead_of_clobbering(self, tmp_path, result):
        write_outputs(result, tmp_path)
        other = crawl(FakeFetcher(), 114, 2, only_departments=["59"])
        write_outputs(other, tmp_path)

        meta = self.read(tmp_path / "meta.json")
        assert [(s["year"], s["sem"]) for s in meta["semesters"]] == [(115, 1), (114, 2)]

        index = self.read(tmp_path / "index.json")
        assert index["course_count"] == 12
        assert {(c["year"], c["sem"]) for c in index["courses"]} == {(115, 1), (114, 2)}
        assert (tmp_path / "115-1").is_dir() and (tmp_path / "114-2").is_dir()

    def test_rerunning_the_same_semester_does_not_duplicate(self, tmp_path, result):
        write_outputs(result, tmp_path)
        write_outputs(result, tmp_path)
        assert self.read(tmp_path / "index.json")["course_count"] == 6
        assert len(self.read(tmp_path / "meta.json")["semesters"]) == 1

    def test_compact_output_is_the_default(self, tmp_path, result):
        write_outputs(result, tmp_path)
        assert "\n  " not in (tmp_path / "index.json").read_text(encoding="utf-8")

    def test_json_is_written_as_utf8_not_escaped(self, tmp_path, result):
        write_outputs(result, tmp_path)
        raw = (tmp_path / "115-1" / "courses" / "59.json").read_text(encoding="utf-8")
        assert "數位影像處理" in raw and "\\u" not in raw


# --------------------------------------------------------------------------
# 學年期自動偵測
# --------------------------------------------------------------------------
def write_meta(out_dir, entries):
    """手工造一份 meta.json,用來假裝「上次抓完是什麼時候」。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps({"semesters": entries}, ensure_ascii=False), encoding="utf-8"
    )


def stamp(hours_ago):
    moment = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc) - timedelta(hours=hours_ago)
    return moment.isoformat().replace("+00:00", "Z")


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
AVAILABLE = [Semester(115, 1), Semester(114, 2)]


class TestDiscoverSemesters:
    def test_reads_the_home_page_once(self):
        fetcher = FakeFetcher()
        assert discover_semesters(fetcher) == [Semester(115, 1), Semester(114, 2)]
        assert fetcher.request_count == 1


class TestSelectSemesters:
    def picked(self, out_dir, **kwargs):
        kwargs.setdefault("now", NOW)
        return [s for s, _ in select_semesters(AVAILABLE, out_dir, **kwargs)]

    def test_newest_semester_is_always_crawled(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": stamp(0.1)},
        ])
        assert self.picked(tmp_path) == [Semester(115, 1)]

    def test_semester_without_data_is_crawled(self, tmp_path):
        write_meta(tmp_path, [{"year": 115, "sem": 1, "generated_at": stamp(0.1)}])
        assert self.picked(tmp_path) == AVAILABLE

    def test_stale_older_semester_is_refreshed(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": stamp(30)},
        ])
        assert self.picked(tmp_path, refresh_after=24) == AVAILABLE

    def test_fresh_older_semester_is_skipped(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": stamp(3)},
        ])
        assert self.picked(tmp_path, refresh_after=24) == [Semester(115, 1)]

    def test_partial_data_does_not_count_as_crawled(self, tmp_path):
        """--dept 的結果是不完整的,不能讓它擋掉之後的完整抓取。"""
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": stamp(1), "partial": True},
        ])
        assert self.picked(tmp_path, refresh_after=24) == AVAILABLE

    def test_force_all_ignores_freshness(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": stamp(0.1)},
        ])
        assert self.picked(tmp_path, force_all=True) == AVAILABLE

    def test_unreadable_timestamp_falls_back_to_crawling(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 115, "sem": 1, "generated_at": stamp(0.1)},
            {"year": 114, "sem": 2, "generated_at": "上個禮拜"},
        ])
        assert self.picked(tmp_path) == AVAILABLE

    def test_missing_meta_file_crawls_everything(self, tmp_path):
        assert self.picked(tmp_path) == AVAILABLE

    def test_nothing_available_selects_nothing(self, tmp_path):
        assert select_semesters([], tmp_path, now=NOW) == []

    def test_semester_no_longer_on_the_home_page_is_left_alone(self, tmp_path):
        """113-2 已經從首頁下架,不該被抓,資料也不該被動到。"""
        write_meta(tmp_path, [{"year": 113, "sem": 2, "generated_at": stamp(999)}])
        assert Semester(113, 2) not in self.picked(tmp_path)

    def test_reason_is_recorded_for_the_log(self, tmp_path):
        reasons = dict(
            (s.path, r) for s, r in select_semesters(AVAILABLE, tmp_path, now=NOW)
        )
        assert reasons["115-1"] == "最新學期"
        assert reasons["114-2"] == "尚無資料"


class TestAutoModeCli:
    def test_without_year_and_sem_it_crawls_what_the_site_offers(
        self, tmp_path, fake_fetcher_factory
    ):
        code = main(["--out", str(tmp_path), "--dept", "59", "--log-level", "ERROR"])
        assert code == 0
        assert (tmp_path / "115-1").is_dir() and (tmp_path / "114-2").is_dir()
        meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert [s["path"] for s in meta["semesters"]] == ["115-1", "114-2"]

    def test_explicit_year_and_sem_skips_discovery(
        self, tmp_path, fake_fetcher_factory
    ):
        main([
            "--year", "115", "--sem", "1",
            "--out", str(tmp_path), "--dept", "59", "--log-level", "ERROR",
        ])
        fetcher = fake_fetcher_factory.created[0]
        assert (0, None) not in fetcher.calls  # 沒去讀首頁
        assert not (tmp_path / "114-2").exists()

    def test_year_without_sem_is_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--year", "115", "--out", str(tmp_path)])


# --------------------------------------------------------------------------
# 回補歷史學期
# --------------------------------------------------------------------------
class TestParseYearRange:
    def test_range(self):
        assert parse_year_range("90-114") == (90, 114)

    def test_single_year(self):
        assert parse_year_range("113") == (113, 113)

    def test_reversed_range_is_normalised(self):
        assert parse_year_range("114-90") == (90, 114)

    def test_whitespace_is_tolerated(self):
        assert parse_year_range("  90-114  ") == (90, 114)

    def test_garbage_raises_with_a_useful_message(self):
        with pytest.raises(ValueError, match="--years 看不懂"):
            parse_year_range("民國九十年")


class TestBackfillSemesters:
    def test_newest_first_and_both_semesters(self, tmp_path):
        picked = [s for s, _ in backfill_semesters((112, 113), tmp_path)]
        assert [s.path for s in picked] == ["113-2", "113-1", "112-2", "112-1"]

    def test_already_crawled_semesters_are_skipped_regardless_of_age(self, tmp_path):
        """過去的學期不會再變,抓過就永久跳過 —— 這讓回補可以分批續跑。"""
        write_meta(tmp_path, [{"year": 113, "sem": 1, "generated_at": stamp(9999)}])
        picked = [s.path for s, _ in backfill_semesters((113, 113), tmp_path)]
        assert picked == ["113-2"]

    def test_partial_data_does_not_block_a_backfill(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 113, "sem": 1, "generated_at": stamp(1), "partial": True},
        ])
        picked = [s.path for s, _ in backfill_semesters((113, 113), tmp_path)]
        assert picked == ["113-2", "113-1"]

    def test_limit_splits_the_work_into_batches(self, tmp_path):
        picked = [s.path for s, _ in backfill_semesters((90, 114), tmp_path, limit=3)]
        assert picked == ["114-2", "114-1", "113-2"]

    def test_force_all_reruns_even_crawled_semesters(self, tmp_path):
        write_meta(tmp_path, [{"year": 113, "sem": 1, "generated_at": stamp(1)}])
        picked = [s.path for s, _ in backfill_semesters((113, 113), tmp_path, force_all=True)]
        assert picked == ["113-2", "113-1"]

    def test_nothing_left_to_do(self, tmp_path):
        write_meta(tmp_path, [
            {"year": 113, "sem": 1, "generated_at": stamp(1)},
            {"year": 113, "sem": 2, "generated_at": stamp(1)},
        ])
        assert backfill_semesters((113, 113), tmp_path) == []


class TestBackfillCli:
    def test_years_and_year_are_mutually_exclusive(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--years", "90-114", "--year", "113", "--sem", "1",
                  "--out", str(tmp_path)])

    def test_bad_year_range_is_rejected_before_any_request(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--years", "亂寫", "--out", str(tmp_path)])

    def test_backfill_does_not_read_the_home_page(self, tmp_path, fake_fetcher_factory):
        code = main([
            "--years", "115", "--max-semesters", "1",
            "--out", str(tmp_path), "--dept", "59", "--log-level", "ERROR",
        ])
        assert code == 0
        fetcher = fake_fetcher_factory.created[0]
        assert (0, None) not in fetcher.calls
        assert (tmp_path / "115-2").is_dir()


class TestSemesterLevelFaultTolerance:
    """一個學期抓不到,不可以賠掉同一批其他學期的成果。

    實測踩過:回補第一批的 114-1 總覽頁連線逾時(台灣時間 02:01,
    疑似學校深夜維護),例外一路往上炸掉整個執行,workflow 的
    Publish 步驟因而被跳過 —— 那批就算已經抓完 11 個學期也全部白做。
    """

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_other_semesters_still_get_written(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(114, 1)}
        code = main([
            "--years", "114", "--out", str(tmp_path),
            "--dept", "59", "--log-level", "ERROR",
        ])
        assert code == 0                       # 有成果就要讓它發布
        assert (tmp_path / "114-2").is_dir()   # 另一個學期照樣寫出來
        assert not (tmp_path / "114-1").exists()

    def test_failure_is_recorded_in_errors_json(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(114, 1)}
        main(["--years", "114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "ERROR"])

        errors = self.read(tmp_path / "errors.json")["errors"]
        entry = next(e for e in errors if (e["year"], e["sem"]) == (114, 1))
        assert entry["stage"] == "semester"
        assert "TimeoutError" in entry["error"]

    def test_failed_semester_is_not_recorded_in_meta(self, tmp_path, fake_fetcher_factory):
        """meta.json 是「我有什麼資料」。留紀錄會讓下次以為抓過了,永不重試。"""
        fake_fetcher_factory.fail_semesters = {(114, 1)}
        main(["--years", "114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "ERROR"])

        meta = self.read(tmp_path / "meta.json")
        assert [s["path"] for s in meta["semesters"]] == ["114-2"]

    def test_failed_semester_is_retried_next_time(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(114, 1)}
        main(["--years", "114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "ERROR"])

        # 兩個學期都會被列出:114-1 是因為抓失敗沒留下資料,114-2 是因為
        # 這裡用 --dept,結果標了 partial(不完整的資料不算數)。
        # 重點是 114-1 沒有被 errors.json 的失敗紀錄擋掉。
        retry = [s.path for s, _ in backfill_semesters((114, 114), tmp_path)]
        assert "114-1" in retry

    def test_a_later_success_clears_the_error(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(114, 1)}
        main(["--years", "114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "ERROR"])
        assert self.read(tmp_path / "errors.json")["error_count"] == 1

        fake_fetcher_factory.fail_semesters = set()
        main(["--years", "114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "ERROR"])
        assert self.read(tmp_path / "errors.json")["errors"] == []

    def test_every_semester_failing_is_a_real_failure(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(114, 1), (114, 2)}
        code = main(["--years", "114", "--out", str(tmp_path),
                     "--dept", "59", "--log-level", "CRITICAL"])
        assert code == 1


class TestConsecutiveFailureCircuitBreaker:
    """對方整體不可用時要早點收手,不要對著連不上的機器重試整批。

    實測:2026-09-03 18:00-18:18 UTC,GitHub runner 完全連不到學校
    (本機同時是通的),一批 12 個學期全滅,花了 11.5 分鐘在重試。
    """

    def test_stops_after_three_consecutive_failures(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {
            (114, 2), (114, 1), (113, 2), (113, 1), (112, 2), (112, 1),
        }
        code = main(["--years", "112-114", "--out", str(tmp_path),
                     "--dept", "59", "--log-level", "CRITICAL"])
        assert code == 1

        # 只該試到第 3 個就停,不是全部 6 個
        errors = json.loads((tmp_path / "errors.json").read_text(encoding="utf-8"))
        attempted = {(e["year"], e["sem"]) for e in errors["errors"]}
        assert len(attempted) == CONSECUTIVE_FAILURE_LIMIT
        assert attempted == {(114, 2), (114, 1), (113, 2)}

    def test_a_success_resets_the_counter(self, tmp_path, fake_fetcher_factory):
        """散落的失敗不該被誤判成「對方掛了」。"""
        fake_fetcher_factory.fail_semesters = {(114, 2), (113, 2), (112, 2)}
        code = main(["--years", "112-114", "--out", str(tmp_path),
                     "--dept", "59", "--log-level", "CRITICAL"])
        assert code == 0
        # 中間夾著成功,計數器歸零,所以 6 個學期全部都試過了
        for path in ("114-1", "113-1", "112-1"):
            assert (tmp_path / path).is_dir()

    def test_already_crawled_semesters_survive_an_abort(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.fail_semesters = {(113, 2), (113, 1), (112, 2)}
        main(["--years", "112-114", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "CRITICAL"])
        # 中止前抓好的 114-2 / 114-1 要留著
        assert (tmp_path / "114-2").is_dir() and (tmp_path / "114-1").is_dir()
        assert not (tmp_path / "112-1").exists()   # 中止後的沒去碰


class TestHalfCrawledSemesterIsNotPublished:
    """斷路器在學期中途跳開時,不可以把半套資料寫出去。

    斷路器讓剩下的單位「直接放棄」而不是「真的去問過」,那些單位的 0 門課
    是假的。寫出去會蓋掉線上完整的資料,而且 meta.json 會記成「剛更新過」,
    把之後 24 小時的重試一起擋掉 —— 一次網路中斷變成一天的資料空洞。
    """

    def test_crawl_raises_instead_of_returning_partial_data(self):
        # -2 總覽頁 + -3 系所頁抓得到,第 3 個請求(第一個班級頁)開始不可用
        fetcher = FakeFetcher(unavailable_after=3)
        with pytest.raises(SiteUnavailable):
            crawl(fetcher, 115, 1, only_departments=["59"])

    def test_nothing_is_written_for_that_semester(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.unavailable_after = 3
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--log-level", "CRITICAL"])
        assert not (tmp_path / "115-1").exists()
        assert not (tmp_path / "meta.json").exists(), "meta.json 記了就會擋住重試"

    def test_the_failure_lands_in_errors_json(self, tmp_path, fake_fetcher_factory):
        fake_fetcher_factory.unavailable_after = 3
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--log-level", "CRITICAL"])
        errors = json.loads((tmp_path / "errors.json").read_text(encoding="utf-8"))
        assert errors["error_count"] == 1
        assert errors["errors"][0]["stage"] == "semester"
        assert "SiteUnavailable" in errors["errors"][0]["error"]
