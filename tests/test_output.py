"""分類輸出:教師 / 班級 / 學程 / 教室 / 時段索引。

每個維度都要能「一個 URL 就查到」,所以這裡驗的重點是:
清單檔數得對、明細檔內容自足、代碼髒掉時不會寫出奇怪的檔名。
"""

from __future__ import annotations

import json
from pathlib import Path

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


class TestSchemaV2Timestamps:
    """schema v2:`generated_at` 只留在 meta.json / errors.json。

    這不是美觀問題。每個檔都帶時間戳的話,每次跑完所有檔案的內容都會變,
    發布時等於整包重推 —— 有了 25 年的歷史資料之後那是每天好幾 GB 的流量。
    """

    def test_semester_files_have_no_timestamp(self, out):
        for path in (out / "115-1").rglob("*.json"):
            assert "generated_at" not in read(path), path

    def test_index_has_no_timestamp(self, out):
        assert "generated_at" not in read(out / "index.json")

    def test_meta_and_errors_still_carry_it(self, out):
        assert "generated_at" in read(out / "meta.json")
        assert "generated_at" in read(out / "meta.json")["semesters"][0]
        assert "generated_at" in read(out / "errors.json")

    def test_rerunning_produces_byte_identical_semester_files(self, tmp_path):
        """同樣的抓取結果重跑一次,學期檔案要一模一樣(才不會產生假 diff)。"""
        result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
        write_outputs(result, tmp_path)
        before = {
            p.relative_to(tmp_path): p.read_bytes()
            for p in (tmp_path / "115-1").rglob("*.json")
        }
        write_outputs(result, tmp_path)
        after = {
            p.relative_to(tmp_path): p.read_bytes()
            for p in (tmp_path / "115-1").rglob("*.json")
        }
        assert before == after


class TestIndexCoverage:
    """schema v2:頂層 index.json 只涵蓋最新的幾個學期。"""

    def semesters(self, tmp_path, pairs):
        for year, sem in pairs:
            write_outputs(
                crawl(FakeFetcher(), year, sem, only_departments=["59"]), tmp_path
            )
        return read(tmp_path / "index.json")

    def test_covers_lists_the_included_semesters(self, tmp_path):
        data = self.semesters(tmp_path, [(115, 1)])
        assert data["covers"] == ["115-1"]

    def test_older_semesters_are_dropped_from_the_top_level_index(self, tmp_path):
        data = self.semesters(tmp_path, [(113, 1), (114, 2), (115, 1)])
        assert data["covers"] == ["115-1", "114-2"]
        assert {(c["year"], c["sem"]) for c in data["courses"]} == {(115, 1), (114, 2)}
        assert data["course_count"] == 12

    def test_dropped_semester_is_still_queryable_per_semester(self, tmp_path):
        self.semesters(tmp_path, [(113, 1), (114, 2), (115, 1)])
        old = read(tmp_path / "113-1" / "index.json")
        assert old["course_count"] == 6
        assert all(c["year"] == 113 for c in old["courses"])

    def test_backfilling_an_old_semester_does_not_disturb_the_index(self, tmp_path):
        self.semesters(tmp_path, [(114, 2), (115, 1)])
        before = (tmp_path / "index.json").read_bytes()
        write_outputs(crawl(FakeFetcher(), 95, 1, only_departments=["59"]), tmp_path)
        assert (tmp_path / "index.json").read_bytes() == before


class TestChangeLog:
    """`changes.json`:一條「最近發生了什麼」的事件流。

    每 4 小時重寫一整包 JSON,光看檔案時間戳分不出「只是重跑」和「學校真的
    動了課」。這個檔要能直接讀開頭幾筆就知道最近的異動 —— 所以是**一筆異動
    一個事件、各自帶時間戳**,不是「這輪 vs 上輪」的批次 diff。
    """

    @pytest.fixture
    def full(self):
        """一份完整(非 --dept)的抓取結果,可以直接改了再寫一次。"""

        def make():
            result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
            result.partial = False  # 讓它看起來像完整抓取,才會產生變更紀錄
            return result

        return make

    def feed(self, out):
        return read(out / "changes.json")

    def events(self, out, kind=None):
        items = self.feed(out)["events"]
        return [e for e in items if kind is None or e["type"] == kind]

    # -- 基本形狀 -----------------------------------------------------------

    def test_first_run_is_a_baseline_not_a_mass_add(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        events = self.events(tmp_path)
        assert len(events) == 1
        assert events[0]["type"] == "baseline"
        assert events[0]["course_count"] == 6
        assert events[0]["semester"] == "115-1"

    def test_an_unchanged_rerun_adds_no_event(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        write_outputs(full(), tmp_path)
        assert len(self.events(tmp_path)) == 1, "沒異動就不該有事件"

    def test_checked_at_advances_even_with_no_events(self, tmp_path, full):
        """「學校沒動」和「爬蟲壞了好幾天沒跑」必須分得出來。"""
        write_outputs(full(), tmp_path)
        first = self.feed(tmp_path)["checked_at"]
        write_outputs(full(), tmp_path)
        assert self.feed(tmp_path)["checked_at"] >= first

    def test_every_event_carries_its_own_timestamp(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        second = full()
        second.courses.pop(0)
        write_outputs(second, tmp_path)

        for event in self.events(tmp_path):
            assert event["at"].endswith("Z")
            assert event["semester"] == "115-1"

    def test_newest_events_come_first(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        second = full()
        second.courses.pop(0)
        write_outputs(second, tmp_path)

        events = self.events(tmp_path)
        assert events[0]["type"] == "course_removed"
        assert events[-1]["type"] == "baseline", "舊事件要留在後面"

    # -- 課程 ---------------------------------------------------------------

    def test_a_removed_course_is_an_event(self, tmp_path, full):
        write_outputs(full(), tmp_path)

        second = full()
        gone = second.courses.pop(0)
        write_outputs(second, tmp_path)

        event = self.events(tmp_path, "course_removed")[0]
        assert event["id"] == gone.id
        assert event["name"] == gone.name_zh
        assert event["department_ids"] == ["59"]

    def test_an_added_course_is_an_event(self, tmp_path, full):
        before = full()
        newcomer = before.courses.pop()
        write_outputs(before, tmp_path)
        write_outputs(full(), tmp_path)

        event = self.events(tmp_path, "course_added")[0]
        assert event["id"] == newcomer.id
        assert event["name"] == newcomer.name_zh
        assert not self.events(tmp_path, "course_removed")

    def test_a_changed_course_shows_before_and_after(self, tmp_path, full):
        write_outputs(full(), tmp_path)

        second = full()
        target = second.courses[0]
        target.teachers = ["新老師"]
        target.teacher_codes = ["99999"]
        write_outputs(second, tmp_path)

        event = self.events(tmp_path, "course_changed")[0]
        assert event["id"] == target.id
        assert event["changes"]["teachers"]["to"] == ["新老師"]
        assert event["changes"]["teachers"]["from"] != ["新老師"]
        assert "credits" not in event["changes"], "沒動到的欄位不該出現"

    def test_a_changed_time_slot_is_caught(self, tmp_path, full):
        """調課是最需要被看見的異動之一。"""
        write_outputs(full(), tmp_path)

        second = full()
        target = next(c for c in second.courses if c.time_slots)
        target.time_slots = []
        write_outputs(second, tmp_path)

        assert "time_slots" in self.events(tmp_path, "course_changed")[0]["changes"]

    # -- 教師 ---------------------------------------------------------------

    def test_a_teacher_who_stops_teaching_is_an_event(self, tmp_path, full):
        """課還在、只是換人上,課程端看不出少了誰 —— 教師端才看得到。"""
        write_outputs(full(), tmp_path)

        second = full()
        target = next(c for c in second.courses if c.teacher_codes == ["12095"])
        target.teachers = ["接手的人"]
        target.teacher_codes = ["99999"]
        write_outputs(second, tmp_path)

        removed = self.events(tmp_path, "teacher_removed")
        added = self.events(tmp_path, "teacher_added")
        assert [e["id"] for e in removed] == ["12095"]
        assert removed[0]["name"] == "白敦文"
        assert removed[0]["course_count"] == 1, "消失前開了幾門課"
        assert removed[0]["department_ids"] == ["59"]
        assert [e["id"] for e in added] == ["99999"]
        assert added[0]["name"] == "接手的人"

    def test_a_teacher_is_keyed_by_code_not_name(self, tmp_path, full):
        """115-1 實測 803 個代碼只對到 801 個姓名,確實有同名老師。

        用姓名當 key 的話,其中一位停開就會誤報成「這位老師消失了」。
        """
        first = full()
        first.courses[0].teachers = ["王小明"]
        first.courses[0].teacher_codes = ["10001"]
        first.courses[1].teachers = ["王小明"]
        first.courses[1].teacher_codes = ["10002"]
        write_outputs(first, tmp_path)

        second = full()
        second.courses[0].teachers = ["王小明"]
        second.courses[0].teacher_codes = ["10001"]
        second.courses[1].teachers = ["王小明"]
        second.courses[1].teacher_codes = ["10002"]
        second.courses.pop(1)  # 同名的其中一位停開
        write_outputs(second, tmp_path)

        removed = self.events(tmp_path, "teacher_removed")
        assert [e["id"] for e in removed] == ["10002"]

    def test_removing_a_course_removes_its_only_teacher(self, tmp_path, full):
        write_outputs(full(), tmp_path)

        second = full()
        gone = next(c for c in second.courses if c.teacher_codes == ["12095"])
        second.courses.remove(gone)
        write_outputs(second, tmp_path)

        assert [e["id"] for e in self.events(tmp_path, "teacher_removed")] == ["12095"]
        assert [e["id"] for e in self.events(tmp_path, "course_removed")] == [gone.id]

    def test_a_teacher_still_teaching_elsewhere_is_not_reported(self, tmp_path, full):
        """只是少開一門課,人還在,不該報成「老師消失了」。"""
        first = full()
        for course in first.courses[:2]:
            course.teachers = ["王正豪"]
            course.teacher_codes = ["10864"]
        write_outputs(first, tmp_path)

        second = full()
        for course in second.courses[:2]:
            course.teachers = ["王正豪"]
            course.teacher_codes = ["10864"]
        second.courses.pop(0)
        write_outputs(second, tmp_path)

        assert not [
            e for e in self.events(tmp_path, "teacher_removed") if e["id"] == "10864"
        ]

    # -- 保護機制 -----------------------------------------------------------

    def test_a_huge_batch_is_collapsed_into_a_summary(self, tmp_path, full, monkeypatch):
        """一次幾百筆會把先前真正的異動整個推出保留範圍,所以要折。"""
        monkeypatch.setattr("crawler.output.CHANGE_BULK_THRESHOLD", 2)
        write_outputs(full(), tmp_path)

        second = full()
        second.courses = second.courses[:1]  # 一口氣少 5 門
        write_outputs(second, tmp_path)

        events = self.events(tmp_path)
        assert events[0]["type"] == "bulk_change"
        assert events[0]["counts"]["course_removed"] == 5
        assert events[0]["event_count"] > 2
        assert events[1]["type"] == "baseline", "先前的事件要留著"

    def test_the_summary_says_which_departments_and_classes(
        self, tmp_path, full, monkeypatch
    ):
        """只有一個總數等於什麼都沒說 —— 人還是得自己 diff 才知道發生什麼事。

        115-1 實際遇過:一次多了 265 門課,摘要只寫「265」,結果還是得去翻
        gh-pages 的 commit 才知道那是學校開了 7 個跨校選課班級。
        """
        monkeypatch.setattr("crawler.output.CHANGE_BULK_THRESHOLD", 2)
        write_outputs(full(), tmp_path)

        second = full()
        second.courses = second.courses[:1]
        write_outputs(second, tmp_path)

        summary = self.events(tmp_path)[0]
        # 資工系的 fixture:5 門課全掛在 59,分佈在 5 個班級。
        # 教師事件也帶 department_ids,所以系所計數涵蓋兩種事件。
        assert list(summary["by_department"]) == ["59"]
        assert summary["by_department"]["59"] == summary["event_count"]
        assert set(summary["by_class"]) == {"2915", "3032", "3138", "3718", "3743"}
        assert all(isinstance(v, int) for v in summary["by_class"].values())

    def test_the_summary_groups_are_ordered_by_size(self, tmp_path, full, monkeypatch):
        """量最大的排前面 —— 異常集中在哪裡要第一眼看到。"""
        monkeypatch.setattr("crawler.output.CHANGE_BULK_THRESHOLD", 2)

        first = full()
        # 讓 3 門課掛到另一個系所,製造出不平均的分佈
        for course in first.courses[:3]:
            course.department_ids = ["14"]
        write_outputs(first, tmp_path)

        second = full()
        second.courses = []
        write_outputs(second, tmp_path)

        by_dept = self.events(tmp_path)[0]["by_department"]
        assert list(by_dept) == sorted(by_dept, key=lambda k: -by_dept[k])
        assert by_dept["59"] > by_dept["14"], "6 門課裡 59 佔 3 門以上"

    def test_the_summary_carries_sample_events(self, tmp_path, full, monkeypatch):
        """樣本要是完整事件,看得到課名,不是只有課號。"""
        monkeypatch.setattr("crawler.output.CHANGE_BULK_THRESHOLD", 2)
        monkeypatch.setattr("crawler.output.CHANGE_SAMPLE_LIMIT", 3)
        write_outputs(full(), tmp_path)

        second = full()
        second.courses = second.courses[:1]
        write_outputs(second, tmp_path)

        samples = self.events(tmp_path)[0]["samples"]
        assert len(samples) == 3
        assert all(s["type"] == "course_removed" for s in samples)
        assert all(s["name"] for s in samples), "樣本要帶課名"

    def test_the_class_breakdown_is_capped(self, tmp_path, full, monkeypatch):
        """班級可能有幾百個,只留量最大的幾個。"""
        monkeypatch.setattr("crawler.output.CHANGE_BULK_THRESHOLD", 2)
        monkeypatch.setattr("crawler.output.CHANGE_GROUP_LIMIT", 2)
        write_outputs(full(), tmp_path)

        second = full()
        second.courses = []
        write_outputs(second, tmp_path)

        assert len(self.events(tmp_path)[0]["by_class"]) == 2

    def test_old_events_are_dropped_at_the_limit(self, tmp_path, full, monkeypatch):
        monkeypatch.setattr("crawler.output.CHANGE_EVENT_LIMIT", 3)
        for n in range(6):
            result = full()
            result.courses = result.courses[: 6 - n]
            write_outputs(result, tmp_path)

        assert len(self.events(tmp_path)) == 3

    def test_partial_crawls_never_write_an_event(self, tmp_path, full):
        """--dept 只抓幾個系所,拿它跟全校索引比會得到「移除兩千門課」。"""
        write_outputs(full(), tmp_path)

        partial = full()
        partial.partial = True
        partial.courses = partial.courses[:1]
        write_outputs(partial, tmp_path)

        assert len(self.events(tmp_path)) == 1

    def test_a_backfilled_old_semester_is_a_baseline(self, tmp_path, full):
        """回補歷史學期時頂層索引裡沒有它 —— 那是「第一次抓」,不是「新增」。"""
        write_outputs(full(), tmp_path)

        old = crawl(FakeFetcher(), 100, 1, only_departments=["59"])
        old.partial = False
        write_outputs(old, tmp_path)

        latest = self.events(tmp_path)[0]
        assert latest["type"] == "baseline"
        assert latest["semester"] == "100-1"

    def test_meta_advertises_the_endpoint(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        paths = {e["path"] for e in read(tmp_path / "meta.json")["endpoints"]}
        assert "changes.json" in paths


class TestEnrollmentSnapshots:
    """修課 / 撤選人數的時間軸。

    明細檔裡的人數是當下的值,每次抓取直接覆蓋。學期結束後那是定案的數字,
    算退選率沒問題;但「哪門課在第幾週被大量退掉」沒留快照就永遠答不了,
    而且錯過了要再等一個學期。
    """

    @pytest.fixture
    def full(self):
        def make():
            result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
            result.partial = False
            return result

        return make

    def index(self, out):
        return read(out / "enrollment.json")

    def snapshots(self, out):
        return self.index(out)["snapshots"]

    def test_a_snapshot_file_is_written_per_day(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        entry = self.snapshots(tmp_path)[0]
        assert entry["path"] == f"115-1/enrollment/{entry['date']}.json"
        assert (tmp_path / entry["path"]).is_file()

    def test_the_snapshot_lists_each_course(self, tmp_path, full):
        result = full()
        write_outputs(result, tmp_path)

        entry = self.snapshots(tmp_path)[0]
        snap = read(tmp_path / entry["path"])
        by_id = {c["id"]: c for c in snap["courses"]}
        for course in result.courses:
            if course.enrolled is not None or course.withdrawn is not None:
                assert by_id[course.id]["enrolled"] == course.enrolled
                assert by_id[course.id]["withdrawn"] == course.withdrawn

    def test_the_index_carries_the_totals(self, tmp_path, full):
        """全校退選率的走勢只靠這一個檔就畫得出來,不必下載逐日快照。"""
        result = full()
        write_outputs(result, tmp_path)

        entry = self.snapshots(tmp_path)[0]
        assert entry["enrolled_total"] == sum(c.enrolled or 0 for c in result.courses)
        assert entry["withdrawn_total"] == sum(
            c.withdrawn or 0 for c in result.courses
        )
        assert entry["semester"] == "115-1"

    def test_same_day_reruns_overwrite_instead_of_piling_up(self, tmp_path, full):
        """一天跑 6 次,留下的該是當天最後一次的狀態,不是 6 筆。"""
        write_outputs(full(), tmp_path)

        second = full()
        second.courses[0].withdrawn = 7
        write_outputs(second, tmp_path)

        entries = self.snapshots(tmp_path)
        assert len(entries) == 1
        snap = read(tmp_path / entries[0]["path"])
        assert {c["id"]: c["withdrawn"] for c in snap["courses"]}[
            second.courses[0].id
        ] == 7

    def test_different_days_accumulate(self, tmp_path, full, monkeypatch):
        write_outputs(full(), tmp_path)
        # 假裝隔天又跑了一次
        index = self.index(tmp_path)
        index["snapshots"][0]["date"] = "2020-01-01"
        index["snapshots"][0]["path"] = "115-1/enrollment/2020-01-01.json"
        (tmp_path / "enrollment.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

        write_outputs(full(), tmp_path)
        dates = [s["date"] for s in self.snapshots(tmp_path)]
        assert len(dates) == 2
        assert dates == sorted(dates, reverse=True), "最新的要排前面"

    def test_old_snapshots_are_dropped_at_the_limit(self, tmp_path, full, monkeypatch):
        monkeypatch.setattr("crawler.output.ENROLLMENT_SNAPSHOT_LIMIT", 2)
        write_outputs(full(), tmp_path)

        index = self.index(tmp_path)
        index["snapshots"] = [
            {**index["snapshots"][0], "date": f"2020-01-0{n}"} for n in (1, 2, 3)
        ]
        (tmp_path / "enrollment.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

        write_outputs(full(), tmp_path)
        assert len(self.snapshots(tmp_path)) == 2

    def test_a_semester_without_headcounts_writes_nothing(self, tmp_path, full):
        """太舊的學期整欄都是空的,寫一份全 null 的快照沒有意義。"""
        result = full()
        for course in result.courses:
            course.enrolled = None
            course.withdrawn = None
        write_outputs(result, tmp_path)

        assert not (tmp_path / "enrollment.json").exists()
        assert not (tmp_path / "115-1" / "enrollment").exists()

    def test_partial_crawls_write_no_snapshot(self, tmp_path, full):
        """--dept 只抓幾個系所,總計會是錯的。"""
        partial = full()
        partial.partial = True
        write_outputs(partial, tmp_path)
        assert not (tmp_path / "enrollment.json").exists()

    def test_headcounts_are_in_the_index_too(self, tmp_path, full):
        """算全校退選率不該需要先下載 60 個系所明細檔。"""
        write_outputs(full(), tmp_path)
        entry = read(tmp_path / "index.json")["courses"][0]
        assert "enrolled" in entry and "withdrawn" in entry

    def test_headcount_changes_do_not_flood_the_change_feed(self, tmp_path, full):
        """加退選期間每 4 小時就有上千門課的人數在動。

        放進 changes.json 會每次都觸發 bulk_change,把真正的結構性異動
        (加開、停開、調課、換老師)整個淹掉 —— 兩者變動頻率差一個數量級。
        """
        write_outputs(full(), tmp_path)
        before = len(read(tmp_path / "changes.json")["events"])

        second = full()
        for course in second.courses:
            course.enrolled = (course.enrolled or 0) + 5
            course.withdrawn = (course.withdrawn or 0) + 1
        write_outputs(second, tmp_path)

        after = read(tmp_path / "changes.json")["events"]
        assert len(after) == before, "人數變動不該產生任何異動事件"
        # 但快照要記下來
        entry = self.snapshots(tmp_path)[0]
        assert entry["withdrawn_total"] == sum(c.withdrawn for c in second.courses)

    def test_meta_advertises_the_endpoints(self, tmp_path, full):
        write_outputs(full(), tmp_path)
        paths = {e["path"] for e in read(tmp_path / "meta.json")["endpoints"]}
        assert "enrollment.json" in paths
        assert "{semester}/enrollment/{date}.json" in paths


class TestIndexLanguage:
    """授課語言在輕量索引裡。

    115-1 全校 2,717 門有 499 門非中文(英語 488、中英雙語 11),而「只看全英語
    授課」是很常見的篩選。學程有 programs.json、教室有 classrooms.json 可以
    反查,語言沒有 —— 不放進索引就得下載 60 個系所明細檔才篩得出來。
    """

    @pytest.fixture
    def full(self):
        def make():
            result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
            result.partial = False
            return result

        return make

    def test_language_is_in_the_index(self, tmp_path, full):
        result = full()
        write_outputs(result, tmp_path)

        entries = {c["id"]: c for c in read(tmp_path / "index.json")["courses"]}
        for course in result.courses:
            assert entries[course.id]["language"] == course.language

    def test_chinese_courses_are_null_not_blank(self, tmp_path, full):
        """空白代表中文。要是 null,不是長度 1 的全形空白字串。"""
        write_outputs(full(), tmp_path)
        values = {c["language"] for c in read(tmp_path / "index.json")["courses"]}
        assert None in values
        assert "英語" in values, "fixture 裡有英語授課的課"
        assert all(v is None or v.strip() for v in values)

    def test_switching_to_english_is_a_change_event(self, tmp_path, full):
        """改成全英語授課會影響選課決定,而且低頻,適合進事件流。"""
        write_outputs(full(), tmp_path)

        second = full()
        target = next(c for c in second.courses if c.language is None)
        target.language = "英語"
        write_outputs(second, tmp_path)

        events = read(tmp_path / "changes.json")["events"]
        changed = [e for e in events if e["type"] == "course_changed"]
        assert len(changed) == 1
        assert changed[0]["id"] == target.id
        assert changed[0]["changes"]["language"] == {"from": None, "to": "英語"}


class TestSchemaEvolutionIsNotAChange:
    """加欄位不是學校改了課,不該產生異動事件。

    比對基準是上一輪發布的索引。程式加了新欄位時,舊索引裡沒有那個 key ——
    若用 .get() 讀成 None,「從 None 變成 英語」就會被當成異動。2026-09-05
    把 language 加進索引時,全校 499 門非中文課會一次全部變成 course_changed,
    直接觸發一筆假的 bulk_change 把真正的異動洗掉。
    """

    @pytest.fixture
    def full(self):
        def make():
            result = crawl(FakeFetcher(), 115, 1, only_departments=["59"])
            result.partial = False
            return result

        return make

    def test_a_field_missing_from_the_baseline_is_not_a_change(self, tmp_path, full):
        write_outputs(full(), tmp_path)

        # 模擬「上一輪的索引還沒有 language 這個欄位」
        path = tmp_path / "index.json"
        index = json.loads(path.read_text(encoding="utf-8"))
        for entry in index["courses"]:
            entry.pop("language", None)
        path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

        before = len(read(tmp_path / "changes.json")["events"])
        write_outputs(full(), tmp_path)
        after = read(tmp_path / "changes.json")["events"]

        assert len(after) == before, "加欄位不該產生任何事件"

    def test_a_field_dropped_from_the_index_is_not_a_change(self, tmp_path, full):
        """欄位被拿掉也一樣是程式改了,不是學校改了課。"""
        write_outputs(full(), tmp_path)
        before = len(read(tmp_path / "changes.json")["events"])

        result = full()
        # 讓這一輪的索引少一個欄位
        import crawler.output as out

        original = out._index_entry

        def trimmed(course, year, sem):
            entry = original(course, year, sem)
            entry.pop("language", None)
            return entry

        out._index_entry = trimmed
        try:
            write_outputs(result, tmp_path)
        finally:
            out._index_entry = original

        assert len(read(tmp_path / "changes.json")["events"]) == before

    def test_a_real_change_to_the_same_field_still_reports(self, tmp_path, full):
        """別修過頭 —— 兩邊都有這個欄位時,真的改了還是要報。"""
        write_outputs(full(), tmp_path)

        second = full()
        target = next(c for c in second.courses if c.language is None)
        target.language = "英語"
        write_outputs(second, tmp_path)

        changed = [
            e for e in read(tmp_path / "changes.json")["events"]
            if e["type"] == "course_changed"
        ]
        assert [e["id"] for e in changed] == [target.id]


class TestAtomicWrites:
    """寫檔是先寫暫存檔再 rename。

    發布步驟設了 always()(抓到一半也把成果推上去),所以磁碟上不可以有
    寫到一半的 JSON —— 那會被原樣推到線上,蓋掉一份好的。
    """

    def test_no_temp_file_is_left_behind(self, out):
        assert not list(out.rglob("*.tmp"))

    def test_a_failed_write_leaves_the_old_file_intact(self, tmp_path, monkeypatch):
        from crawler import output

        path = tmp_path / "index.json"
        output._write_json(path, {"schema_version": 3, "courses": []}, False)
        good = path.read_bytes()

        def explode(self, *args, **kwargs):
            raise OSError("模擬:磁碟寫到一半炸掉")

        monkeypatch.setattr(Path, "write_text", explode)
        with pytest.raises(OSError):
            output._write_json(path, {"schema_version": 3, "courses": [1]}, False)

        assert path.read_bytes() == good, "舊檔必須原封不動"
        assert not list(tmp_path.glob("*.tmp")), "失敗時也要收掉暫存檔"
