"""對外 API 的資料契約。

這些 dataclass 的欄位就是 JSON 的欄位。**改欄位等於改 API**,
請一併升 config.SCHEMA_VERSION 並在 README 說明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .periods import DAY_NAMES

# --------------------------------------------------------------------------
# 必選修符號對照
#
# 來源:Cprog.jsp?format=-5 課程標準頁(plan.md §3 Phase 3)。
# 注意 ★ 與 ☆ **都是選修** —— 兩者差別在「共同 / 專業」,不是「必 / 選」。
# --------------------------------------------------------------------------
REQUIREMENT_SYMBOLS: dict[str, tuple[bool, str]] = {
    "○": (True, "部訂共同必修"),
    "△": (True, "校訂共同必修"),
    "☆": (False, "共同選修"),
    "●": (True, "部訂專業必修"),
    "▲": (True, "校訂專業必修"),
    "★": (False, "專業選修"),
}


def requirement_table() -> list[dict[str, Any]]:
    """輸出必選修符號對照表,放進 meta.json。"""
    return [
        {
            "symbol": symbol,
            "required": required,
            "requirement_type": label,
        }
        for symbol, (required, label) in REQUIREMENT_SYMBOLS.items()
    ]


@dataclass
class TimeSlot:
    """一門課在某一天的上課節次。"""

    day: int  # 0=日, 1=一, ..., 6=六
    periods: list[str]  # ["3", "4"] 或 ["N"],保留原始代碼字元

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.day]

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "day_name": self.day_name,
            "periods": list(self.periods),
        }


@dataclass
class Course:
    """一門課。對應課程列表頁(format=-4)的一列。"""

    id: str  # 課號,例 "364893"
    name_zh: str
    name_en: str | None = None  # 課程列表頁與教學大綱頁都沒有英文名,目前恆為 None
    stage: str | None = None  # 階段,例 "1"
    credits: float | None = None
    hours: int | None = None
    required: bool | None = None  # 必=True / 選=False;欄位空白時為 None,不預設 False
    requirement_type: str | None = None  # 例 "專業選修" / "部訂共同必修"
    teachers: list[str] = field(default_factory=list)
    teacher_codes: list[str] = field(default_factory=list)  # Teach.jsp 的 code
    classes: list[str] = field(default_factory=list)  # 開課班級名稱,例 ["資工四"]
    # class_ids / department_ids 不在 HTML 裡,由 main.py 依抓取路徑補上。
    # 有了它們,合開課程才能對回 departments.json,index.json 也才有 dept 可填。
    class_ids: list[str] = field(default_factory=list)
    department_ids: list[str] = field(default_factory=list)
    time_slots: list[TimeSlot] = field(default_factory=list)
    classrooms: list[str] = field(default_factory=list)
    classroom_codes: list[str] = field(default_factory=list)  # Croom.jsp 的 code
    quota: int | None = None  # 人數
    withdrawn: int | None = None  # 撤選人數
    language: str | None = None  # 授課語言,空白代表中文
    syllabus_url: str | None = None
    notes: str | None = None  # 備註,例 "資工四和資工所合開"
    audit: str | None = None  # 隨班附讀
    lab: str | None = None  # 實驗 / 實習
    programs: list[str] = field(default_factory=list)  # 跨領域學程 / 微學程

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name_zh": self.name_zh,
            "name_en": self.name_en,
            "stage": self.stage,
            "credits": self.credits,
            "hours": self.hours,
            "required": self.required,
            "requirement_type": self.requirement_type,
            "teachers": list(self.teachers),
            "teacher_codes": list(self.teacher_codes),
            "classes": list(self.classes),
            "class_ids": list(self.class_ids),
            "department_ids": list(self.department_ids),
            "time_slots": [slot.to_dict() for slot in self.time_slots],
            "classrooms": list(self.classrooms),
            "classroom_codes": list(self.classroom_codes),
            "quota": self.quota,
            "withdrawn": self.withdrawn,
            "language": self.language,
            "syllabus_url": self.syllabus_url,
            "notes": self.notes,
            "audit": self.audit,
            "lab": self.lab,
            "programs": list(self.programs),
        }

    def merge_from(self, other: "Course") -> None:
        """把同課號的另一筆合併進來(合開課程會出現在多個班級頁)。

        只做聯集,不覆寫既有的純量欄位 —— 同課號的課程資料本來就該一致,
        若不一致,以先抓到的那筆為準比較可預期。
        """
        for name in (
            "classes",
            "class_ids",
            "department_ids",
            "teachers",
            "teacher_codes",
            "classrooms",
            "classroom_codes",
            "programs",
        ):
            merged = list(getattr(self, name))
            for value in getattr(other, name):
                if value not in merged:
                    merged.append(value)
            setattr(self, name, merged)


@dataclass
class Department:
    """系所 / 行政單位。對應總覽頁(format=-2)的一個連結。"""

    id: str  # 例 "59"
    name: str  # 例 "資工系"
    college: str | None  # 學院,例 "電資學院";行政單位為 None
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "college": self.college,
            "url": self.url,
        }


@dataclass
class ClassGroup:
    """班級。對應單位頁(format=-3)的一個連結。

    注意 id 與 Department.id 是**兩組不同的 ID**,無法互推,
    一定要從 format=-3 頁面解析出來(plan.md §1.3 陷阱 2)。
    """

    id: str  # 例 "2915"
    name: str  # 例 "資工四"
    department_id: str  # 例 "59"
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "department_id": self.department_id,
            "url": self.url,
        }
