"""對外 API 的資料契約。

這些 dataclass 的欄位就是 JSON 的欄位。**改欄位等於改 API**,
請一併升 config.SCHEMA_VERSION 並在 README 說明。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .periods import DAY_NAMES

# --------------------------------------------------------------------------
# 必選修符號對照
#
# 來源:Cprog.jsp?format=-5 課程標準頁(reference.md「站台結構」)。
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
    #: 修課人數(原始頁面的「人」欄)。**不是名額上限** —— 114-2 全校有 111 種
    #: 不同的值、只有 21% 是 5 的倍數,名額上限不會長這樣。
    enrolled: int | None = None
    withdrawn: int | None = None  # 撤選人數(原始頁面的「撤」欄)
    language: str | None = None  # 授課語言,空白代表中文
    syllabus_url: str | None = None
    notes: str | None = None  # 備註,例 "資工四和資工所合開"
    audit: str | None = None  # 隨班附讀
    lab: str | None = None  # 實驗 / 實習
    programs: list[str] = field(default_factory=list)  # 跨領域學程 / 微學程

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            out[key] = value
            if key == "enrolled":
                # `quota` 是 `enrolled` 的舊名,語意上一直是修課人數而不是名額上限。
                # 名字會誤導拿它算比率的人(以為分母是容量),所以改名;舊欄位保留
                # 不刪 —— README 的相容性承諾是「只新增、不改既有欄位」。
                out["quota"] = value
        # asdict 只看欄位,TimeSlot 的 day_name 是 property,要走它自己的 to_dict。
        out["time_slots"] = [slot.to_dict() for slot in self.time_slots]
        return out

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
        return asdict(self)


@dataclass
class ClassGroup:
    """班級。對應單位頁(format=-3)的一個連結。

    注意 id 與 Department.id 是**兩組不同的 ID**,無法互推,
    一定要從 format=-3 頁面解析出來(reference.md「站台結構」的陷阱 2)。
    """

    id: str  # 例 "2915"
    name: str  # 例 "資工四"
    department_id: str  # 例 "59"
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, order=True)
class Semester:
    """一個學年期。

    `order=True` 讓它可以直接排序:欄位順序 (year, sem) 就是「新舊」的定義,
    所以 `sorted(semesters, reverse=True)` 得到的就是「最新的在前面」。
    frozen 是為了能放進 set / dict key(要判斷「這學期抓過了沒」)。
    """

    year: int  # 民國學年度,例 115
    sem: int  # 1 或 2

    @property
    def path(self) -> str:
        """輸出目錄名,也是 API 路徑的一段,例 "115-1"。"""
        return f"{self.year}-{self.sem}"

    @property
    def label(self) -> str:
        return f"{self.year} 學年度第 {self.sem} 學期"

    def to_dict(self) -> dict[str, Any]:
        return {"year": self.year, "sem": self.sem, "path": self.path}
