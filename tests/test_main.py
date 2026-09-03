"""Phase 4 驗收:抓取流程、去重合併、輸出結構、錯誤處理。

完全離線 —— 用假的 Fetcher 直接回傳 Phase 0 的 fixture。
"""

from __future__ import annotations

import json

import pytest

from crawler.config import SCHEMA_VERSION
from crawler.main import crawl, main, write_outputs
from tests.conftest import load_fixture


class FakeFetcher:
    """依 format / code 回傳對應 fixture 的假抓取器。"""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
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

        if code in self.fail_on:
            raise RuntimeError(f"模擬 {code} 抓取失敗")

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
            self.created: list[FakeFetcher] = []

        def __call__(self, **kwargs):
            fetcher = FakeFetcher(fail_on=self.fail_on)
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
        assert set(entry) == {
            "id", "name_zh", "teachers", "time_slots",
            "department_ids", "credits", "year", "sem",
        }

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
