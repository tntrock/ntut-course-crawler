"""分類輸出:教師 / 班級 / 學程 / 教室 / 時段索引。

每個維度都要能「一個 URL 就查到」,所以這裡驗的重點是:
清單檔數得對、明細檔內容自足、代碼髒掉時不會寫出奇怪的檔名。
"""

from __future__ import annotations

import json

import pytest

from crawler.main import crawl, write_outputs
from tests.test_main import FakeFetcher


@pytest.fixture
def out(tmp_path):
    """只抓資工系:5 個班級、6 門課(其中「體育」沒有教師也沒有教室)。"""
    result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
    write_outputs(result, tmp_path, pretty=True)
    return tmp_path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestTeachers:
    def test_index_lists_every_teacher(self, out):
        data = read(out / "115-1" / "teachers.json")
        assert data["teacher_count"] == 5  # 6 門課,「體育」沒有掛老師
        names = {t["name"] for t in data["teachers"]}
        assert names == {"張世豪", "王正豪", "陳香君", "謝東儒", "白敦文"}

    def test_index_points_at_the_detail_file(self, out):
        data = read(out / "115-1" / "teachers.json")
        entry = next(t for t in data["teachers"] if t["name"] == "白敦文")
        assert entry["id"] == "12095"
        assert entry["path"] == "115-1/teachers/12095.json"
        assert (out / entry["path"]).is_file()

    def test_detail_file_is_self_contained(self, out):
        data = read(out / "115-1" / "teachers" / "12095.json")
        assert data["teacher"]["name"] == "白敦文"
        assert data["teacher"]["department_ids"] == ["59"]
        assert "Teach.jsp" in data["teacher"]["url"]
        assert data["course_count"] == 1
        course = data["courses"][0]
        # 明細檔放完整課程物件,拿到就能直接顯示,不用再載別的檔
        assert course["name_zh"] == "數位影像處理"
        assert course["syllabus_url"].startswith("https://aps.ntut.edu.tw/")

    def test_courses_without_a_teacher_produce_no_file(self, out):
        """「體育」沒有教師連結,不該憑空生出一個教師。"""
        data = read(out / "115-1" / "teachers.json")
        assert all(t["id"] is not None for t in data["teachers"])
        assert len(list((out / "115-1" / "teachers").glob("*.json"))) == 5

    def test_teacher_without_code_is_listed_but_has_no_detail_file(self, tmp_path):
        result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
        # 模擬「有姓名、沒有 Teach.jsp 連結」的欄位(體育、班週會會走這條)
        result.courses[0].teachers = ["王小明"]
        result.courses[0].teacher_codes = [""]
        write_outputs(result, tmp_path)

        data = read(tmp_path / "115-1" / "teachers.json")
        entry = next(t for t in data["teachers"] if t["name"] == "王小明")
        assert entry["id"] is None
        assert entry["path"] is None
        assert len(list((tmp_path / "115-1" / "teachers").glob("*.json"))) == 5


class TestClasses:
    def test_index_lists_every_class_group(self, out):
        data = read(out / "115-1" / "classes.json")
        assert data["class_count"] == 5
        assert {c["name"] for c in data["classes"]} == {
            "資工四", "資工三", "資工二", "資工一", "資工所"
        }

    def test_index_carries_the_department_back_reference(self, out):
        data = read(out / "115-1" / "classes.json")
        entry = next(c for c in data["classes"] if c["id"] == "2915")
        assert entry["department_id"] == "59"
        assert entry["department_name"] == "資工系"
        assert entry["college"] == "電資學院"
        assert entry["course_count"] == 6

    def test_detail_file_has_the_whole_timetable(self, out):
        data = read(out / "115-1" / "classes" / "2915.json")
        assert data["class_group"]["name"] == "資工四"
        assert data["course_count"] == 6
        assert len(data["courses"]) == 6


class TestPrograms:
    def test_groups_courses_by_program_name(self, out):
        data = read(out / "115-1" / "programs.json")
        by_name = {p["name"]: p for p in data["programs"]}
        assert by_name["人工智慧科技學程"]["course_ids"] == ["364892", "364893"]
        assert by_name["無人機微學程"]["course_count"] == 1

    def test_program_count_matches(self, out):
        data = read(out / "115-1" / "programs.json")
        assert data["program_count"] == len(data["programs"])


class TestClassrooms:
    def test_lists_rooms_with_their_codes(self, out):
        data = read(out / "115-1" / "classrooms.json")
        by_name = {r["name"]: r for r in data["classrooms"]}
        assert by_name["六教727(e)"]["id"] == "452"
        assert by_name["六教727(e)"]["course_ids"] == ["364893"]
        assert "Croom.jsp" in by_name["六教727(e)"]["url"]

    def test_courses_without_a_classroom_are_not_counted(self, out):
        data = read(out / "115-1" / "classrooms.json")
        assert data["classroom_count"] == 5  # 6 門課,「體育」沒有教室


class TestSchedule:
    def test_buckets_courses_by_day_and_period(self, out):
        data = read(out / "115-1" / "schedule.json")
        wednesday = next(d for d in data["days"] if d["day"] == 3)
        assert wednesday["day_name"] == "三"
        period2 = next(p for p in wednesday["periods"] if p["code"] == "2")
        assert period2["course_ids"] == ["361345", "361351"]

    def test_periods_follow_the_meta_order_not_alphabetical(self, out):
        """節次是 1-9 再 N 再 A-D。照字典序排會把 A 排到 9 前面。"""
        data = read(out / "115-1" / "schedule.json")
        wednesday = next(d for d in data["days"] if d["day"] == 3)
        codes = [p["code"] for p in wednesday["periods"]]
        assert codes == ["2", "3", "4", "5", "6", "7", "8", "9", "A"]

    def test_courses_without_time_slots_are_absent(self, out):
        data = read(out / "115-1" / "schedule.json")
        listed = {
            cid
            for day in data["days"]
            for period in day["periods"]
            for cid in period["course_ids"]
        }
        assert "361339" not in listed  # 體育沒有上課時間


class TestSemesterIndex:
    def test_only_contains_this_semester(self, out):
        data = read(out / "115-1" / "index.json")
        assert data["year"] == 115 and data["sem"] == 1
        assert data["course_count"] == 6
        assert all(c["year"] == 115 for c in data["courses"])


class TestDepartmentsExtras:
    def test_colleges_group_departments(self, out):
        data = read(out / "115-1" / "departments.json")
        assert {"name": "電資學院", "department_ids": ["59"]} in data["colleges"]

    def test_department_entry_points_at_its_course_file(self, out):
        data = read(out / "115-1" / "departments.json")
        entry = data["departments"][0]
        assert entry["path"] == "115-1/courses/59.json"
        assert (out / entry["path"]).is_file()


class TestMetaEndpoints:
    def test_meta_lists_the_endpoints(self, out):
        meta = read(out / "meta.json")
        paths = {e["path"] for e in meta["endpoints"]}
        assert "{semester}/teachers/{teacher_id}.json" in paths
        assert "{semester}/schedule.json" in paths

    def test_meta_names_the_latest_semester(self, out):
        assert read(out / "meta.json")["latest"] == "115-1"


class TestUnsafeIds:
    """代碼是從 HTML 抓來的字串,不能無條件拿去組檔名。"""

    def test_path_traversal_id_does_not_escape_the_output_dir(self, tmp_path, caplog):
        result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
        result.courses[0].teachers = ["壞蛋"]
        result.courses[0].teacher_codes = ["../../pwned"]
        write_outputs(result, tmp_path)

        assert not (tmp_path.parent / "pwned.json").exists()
        entry = next(
            t for t in read(tmp_path / "115-1" / "teachers.json")["teachers"]
            if t["name"] == "壞蛋"
        )
        assert entry["path"] is None
        assert "不能當檔名" in caplog.text


class TestStaleFiles:
    def test_full_crawl_removes_files_from_a_previous_run(self, tmp_path):
        """班級消失(改班號、系所裁撤)時,舊檔不能留在原地假裝還有效。"""
        stale = tmp_path / "115-1" / "classes" / "9999.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", encoding="utf-8")

        result = crawl(FakeFetcher(), 115, 1)
        assert result.partial is False
        write_outputs(result, tmp_path)

        assert not stale.exists()

    def test_partial_crawl_keeps_other_departments(self, tmp_path, caplog):
        """--dept 是局部抓取,不可以把沒抓的系所檔案清掉。"""
        keep = tmp_path / "115-1" / "courses" / "30.json"
        keep.parent.mkdir(parents=True)
        keep.write_text("{}", encoding="utf-8")

        result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
        write_outputs(result, tmp_path)

        assert keep.exists()
        assert "不清除舊檔" in caplog.text
