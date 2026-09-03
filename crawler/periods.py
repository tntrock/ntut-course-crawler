"""上課節次代碼 ↔ 實際時間的對照與解析。

節次對照表就印在課程列表頁底部(見 tests/fixtures/course_list_real.html),
這裡的資料是照抄該表,不是推測。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 節次代碼 → (開始, 結束)。順序即為一天之中的先後順序。
PERIOD_TIMES: dict[str, tuple[str, str]] = {
    "1": ("08:10", "09:00"),
    "2": ("09:10", "10:00"),
    "3": ("10:10", "11:00"),
    "4": ("11:10", "12:00"),
    "N": ("12:10", "13:00"),
    "5": ("13:10", "14:00"),
    "6": ("14:10", "15:00"),
    "7": ("15:10", "16:00"),
    "8": ("16:10", "17:00"),
    "9": ("17:10", "18:00"),
    "A": ("18:30", "19:20"),
    "B": ("19:20", "20:10"),
    "C": ("20:20", "21:10"),
    "D": ("21:10", "22:00"),
}

#: 課程表格的星期欄位標題,順序就是表格欄位順序
DAY_NAMES: tuple[str, ...] = ("日", "一", "二", "三", "四", "五", "六")

#: 一列課程有幾個星期欄位
DAY_COUNT = len(DAY_NAMES)

#: 空白欄位可能出現的字元:半形空白、全形空白(U+3000)、不斷行空白
_BLANK_CHARS = " \t\r\n\u3000\xa0"


def period_table() -> list[dict[str, str]]:
    """輸出節次對照表,供 meta.json 給下游渲染課表用。"""
    return [
        {"code": code, "start": start, "end": end}
        for code, (start, end) in PERIOD_TIMES.items()
    ]


def parse_period_cell(text: str) -> list[str]:
    """把一格星期欄位的文字轉成節次代碼串列。

    實際資料長得像 `" 2 3 4"`、`" 8 9 A"`,空白格是全形空白 `　`。
    每個節次代碼都是**單一字元**,所以直接逐字元取,這樣就算哪天
    學校把分隔空白拿掉(`"234"`)也不會解錯。

    未知代碼會保留(不丟資料)但記 warning,學校若新增節次我們會看到。
    """
    if not text:
        return []

    codes: list[str] = []
    for char in text:
        if char in _BLANK_CHARS:
            continue
        upper = char.upper()
        if upper not in PERIOD_TIMES:
            log.warning("未知的節次代碼 %r(原文 %r),仍予保留", char, text)
        codes.append(upper)
    return codes
