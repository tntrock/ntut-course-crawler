"""回補的排程,以及「醒來但沒事做」時的守門。

排程補完 96-1 之後仍然會每 6 小時醒來一次。這個檔守的是**空轉的那幾次
什麼都不要留下**,尤其是不可以寫 `runs.json` —— 它只保留最近 120 筆,
一天 4 筆空紀錄幾天就會把真正的執行紀錄整個擠光,狀態頁跟著失真。

另一件事是排程與手動的參數不可以混用。`inputs.x || 預設` 這種寫法在
手動 dispatch 且把 boolean 取消勾選時會讀成「沒給」,於是預設值把使用者
明確關掉的選項又打開 —— 所以一律用 `github.event_name` 明確分流。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
BACKFILL = WORKFLOWS_DIR / "backfill.yml"
CAPACITY = WORKFLOWS_DIR / "capacity.yml"

#: 守門步驟的 id。後面每一個步驟都必須引用它的輸出。
GUARD_ID = "remaining"


def workflow() -> dict:
    return yaml.safe_load(BACKFILL.read_text(encoding="utf-8"))


def steps() -> list[dict]:
    return workflow()["jobs"]["backfill"]["steps"]


def guard_index(items: list[dict]) -> int:
    for i, step in enumerate(items):
        if step.get("id") == GUARD_ID:
            return i
    raise AssertionError(f"找不到 id 為 {GUARD_ID!r} 的守門步驟")


class TestSchedule:
    def test_backfill_runs_on_a_schedule(self) -> None:
        """沒有排程的話,整個序列就得靠有人在線上一批一批按。"""
        # yaml 會把 `on:` 解析成布林 True,不是字串 "on"
        triggers = workflow()[True]
        assert "schedule" in triggers
        assert triggers["schedule"][0]["cron"]

    def test_scheduled_runs_do_not_read_dispatch_inputs(self) -> None:
        """排程時 inputs 全是空的,必須用 event_name 明確分流。

        寫成 `inputs.with_syllabus || 'true'` 的話,手動 dispatch 且取消
        勾選時會被預設值又打開 —— 那是安靜地做了使用者沒要求的事。
        """
        env = workflow()["jobs"]["backfill"]["env"]
        for key in ("YEARS", "WITH_SYLLABUS", "DELAY", "MAX_SEMESTERS"):
            assert "github.event_name" in str(env[key]), (
                f"{key} 直接讀 inputs,排程觸發時會拿到空值"
            )


class TestGuard:
    def test_a_guard_step_counts_what_is_left(self) -> None:
        assert guard_index(steps()) >= 0

    def test_the_guard_runs_after_the_state_files_are_restored(self) -> None:
        """判斷「還有沒有學期要補」要讀 meta.json 與 syllabus.json,
        那兩個檔是上一個步驟從 gh-pages 還原回來的。"""
        items = steps()
        restore = next(
            i for i, s in enumerate(items) if "Restore" in (s.get("name") or "")
        )
        assert guard_index(items) > restore

    @pytest.mark.parametrize(
        "name",
        ["Backfill", "Record this run", "Add landing pages", "Publish to gh-pages"],
    )
    def test_the_expensive_steps_are_named_as_expected(self, name: str) -> None:
        """下面那條「守門之後全部要有條件」的斷言,前提是這幾步真的存在。"""
        assert any((s.get("name") or "") == name for s in steps())

    def test_every_step_after_the_guard_is_conditioned_on_it(self) -> None:
        """這是這個檔真正要守的東西。

        逐一列出步驟名稱去斷言,日後有人在中間加一步就會漏掉;所以改成
        「守門之後的每一步都必須引用守門的輸出」。空轉的排程因此不會
        寫 runs.json、不會發布、也不會上傳 artifact。
        """
        items = steps()
        for step in items[guard_index(items) + 1 :]:
            condition = str(step.get("if", ""))
            assert f"steps.{GUARD_ID}.outputs" in condition, (
                f"步驟 {step.get('name')!r} 沒有掛守門條件,"
                "沒事做的排程也會執行它"
            )


class TestCapacityWorkflow:
    def workflow(self) -> dict:
        return yaml.safe_load(CAPACITY.read_text(encoding="utf-8"))

    def test_runs_monthly_and_on_demand(self) -> None:
        triggers = self.workflow()[True]     # yaml 把 `on:` 解析成布林 True
        assert triggers["schedule"][0]["cron"] == "0 2 1 * *"
        assert "workflow_dispatch" in triggers

    def test_shares_the_crawl_concurrency_group(self) -> None:
        """不可以跟其他抓取同時對學校發請求。"""
        assert self.workflow()["concurrency"]["group"] == "crawl"

    def test_actually_asks_for_capacity(self) -> None:
        assert "--with-capacity" in CAPACITY.read_text(encoding="utf-8")


class TestCapacityRestoredEverywhere:
    """漏還原 capacity.json 的後果不是「這次抓不到」,是安靜地用空值蓋掉
    全站的教室容量:crawler.main 的 crawl_capacity 靠 read_capacity(out_dir)
    帶入上一次的結果,crawler.output 的 write_classrooms 也靠它決定
    classrooms.json 裡每個房間的 capacity —— 沒有還原,兩邊拿到的都是空字典。

    逐一列出 workflow 檔名的話,以後新增一支忘記還原也不會被抓到,所以
    改成掃過 `.github/workflows/*.yml` 裡每一個真的有
    「Restore shared index files」步驟的檔案。
    """

    def restore_steps(self) -> list[tuple[Path, dict]]:
        found: list[tuple[Path, dict]] = []
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job in (doc.get("jobs") or {}).values():
                for step in job.get("steps") or []:
                    if "Restore shared index files" in (step.get("name") or ""):
                        found.append((path, step))
        return found

    def test_there_are_restore_steps_to_check(self) -> None:
        """保護測試本身:掃描邏輯壞掉、找不到任何 restore 步驟的話,
        底下的迴圈會什麼都不驗證就安靜通過。"""
        assert len(self.restore_steps()) >= 4

    def test_every_restore_step_also_restores_capacity_json(self) -> None:
        for path, step in self.restore_steps():
            assert "capacity.json" in step.get("run", ""), (
                f"{path.name} 的 Restore shared index files 沒有還原 "
                "capacity.json,下一次發布會用空值覆蓋掉全站的教室容量"
            )
