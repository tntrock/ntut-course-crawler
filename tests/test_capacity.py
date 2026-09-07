from datetime import datetime, timedelta, timezone
import json

from crawler.main import (
    DEFAULT_CAPACITY_REFRESH_AFTER,
    classroom_targets,
    select_capacity_targets,
)


def write_semester_classrooms(out_dir, semester, entries):
    """造出一個學期的 classrooms.json(只放測試需要的欄位)。"""
    d = out_dir / semester
    d.mkdir(parents=True, exist_ok=True)
    (d / "classrooms.json").write_text(
        json.dumps({"schema_version": 3, "classrooms": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


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
