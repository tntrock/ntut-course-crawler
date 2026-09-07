"""解析教室頁(`Croom.jsp?format=-3`)。

版面是一張表:兩列 `<th>` 表頭(含 rowspan/colspan),接著一列 `<td>` 資料 ——
教室簡稱、教室全名、容量(座位數),後面六格是日間/夜間/週末的節數與使用率。

**只取容量。** 使用率會隨課表變動、與 schedule.json 資訊重疊;週課表的內容
我們已經從課表頁拿到了。見設計文件的「刻意不做」。

實測 `year` / `sem` 只影響使用率與週課表,不影響容量 —— 打不存在的學年度
(year=199)仍然回傳正確容量。所以容量是教室主檔的屬性。
"""

from __future__ import annotations

import logging
from typing import Any

from .parse_util import clean, soup_of

log = logging.getLogger("crawler.parse_croom")

#: 資料列至少要有這幾格才讀得到容量(簡稱、全名、容量)。
_MIN_CELLS = 3

EMPTY: dict[str, Any] = {"name": None, "full_name": None, "capacity": None}


def parse_classroom(html: str) -> dict[str, Any]:
    """吃教室頁的 HTML,回傳 `{name, full_name, capacity}`。

    解析不出來時每個欄位都是 `None`,**不拋例外** —— 呼叫端要能記下錯誤
    然後繼續抓下一間,不能因為一間教室的版面怪就中斷整批。
    """
    table = soup_of(html).find("table")
    if table is None:
        log.warning("教室頁沒有表格,可能是錯誤頁或版面改了")
        return dict(EMPTY)

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < _MIN_CELLS:
            continue  # 表頭列只有 <th>,資料列才有 <td>
        return {
            "name": clean(cells[0].get_text()),
            "full_name": clean(cells[1].get_text()),
            "capacity": _to_int(clean(cells[2].get_text())),
        }

    log.warning("教室頁找不到資料列")
    return dict(EMPTY)


def _to_int(text: str | None) -> int | None:
    """數字讀不出來就回 None。**不要回 0** —— 「沒讀到」和「零個座位」
    是兩回事,填 0 會讓使用端算出無限大的使用率。"""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        log.warning("容量欄位不是數字:%r", text)
        return None
