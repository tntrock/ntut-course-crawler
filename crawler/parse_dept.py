"""解析總覽頁(format=-2)與單位頁(format=-3)。

純函式:吃 HTML 字串 → 吐 dataclass,不發任何網路請求。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from .models import ClassGroup, Department
from .parse_util import absolute_url, clean, query_param, soup_of

log = logging.getLogger(__name__)


def parse_colleges(html: str) -> list[Department]:
    """解析總覽頁,回傳所有系所 / 行政單位。

    版面特性(plan.md §3 實測結果):
    - 學院名稱只出現一次,靠 `rowspan` 涵蓋後面幾列,所以要自己追 rowspan
      才能把系所對回學院。
    - 第一列是行政單位(教務處、體育室…),學院欄是全形空白 → college=None。
    - 學院區塊之間夾著 `<tr><td colspan=6>` 的空白分隔列。
    - 陷阱:延續列的第一格可能就是一個系所連結(例:管理學院 code=C2),
      不能用「第一格永遠是學院」這種規則。
    """
    soup = soup_of(html)
    table = soup.find("table")
    if table is None:
        log.warning("總覽頁找不到 <table>,回傳空清單")
        return []

    departments: list[Department] = []
    seen: set[str] = set()
    college: str | None = None
    pending = 0  # 還有幾列屬於目前這個學院(由 rowspan 決定)

    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue

        if pending > 0:
            # 延續列:整列都是系所,沒有學院欄
            link_cells = cells
            pending -= 1
        else:
            head = cells[0]
            college = clean(head.get_text())
            pending = max(0, _rowspan(head) - 1)
            link_cells = cells[1:]

        for cell in link_cells:
            for anchor in cell.find_all("a"):
                dept = _department_from_anchor(anchor, college)
                if dept is None:
                    continue
                if dept.id in seen:
                    log.warning("總覽頁出現重複的單位代碼 %s(%s),略過", dept.id, dept.name)
                    continue
                seen.add(dept.id)
                departments.append(dept)

    if not departments:
        log.warning("總覽頁沒有解析到任何單位,版面可能已改版")
    return departments


def parse_class_groups(html: str, department_id: str) -> list[ClassGroup]:
    """解析單位頁,回傳該單位底下的班級。

    `department_id` 必須由呼叫端傳入 —— 頁面上只有班級代碼(例 2915),
    沒有系所代碼(例 59),兩者是伺服器分別配發的 ID,無法互推
    (plan.md §1.3 陷阱 2)。
    """
    soup = soup_of(html)

    groups: list[ClassGroup] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if "Subj.jsp" not in href:
            continue
        code = query_param(href, "code")
        name = clean(anchor.get_text())
        if not code or not name:
            log.warning("單位頁 %s 有無法解析的班級連結:%r", department_id, href)
            continue
        if code in seen:
            continue
        seen.add(code)
        groups.append(
            ClassGroup(
                id=code,
                name=name,
                department_id=department_id,
                url=absolute_url(href),
            )
        )

    if not groups:
        log.warning("單位 %s 沒有任何班級", department_id)
    return groups


def _rowspan(cell) -> int:
    raw = cell.get("rowspan")
    if raw is None:
        return 1
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("無法解析的 rowspan=%r,當作 1", raw)
        return 1


def _department_from_anchor(anchor, college: str | None) -> Department | None:
    href = anchor.get("href") or ""
    if "Subj.jsp" not in href:
        return None
    code = query_param(href, "code")
    name = clean(anchor.get_text())
    if not code or not name:
        log.warning("總覽頁有無法解析的單位連結:%r", href)
        return None
    return Department(id=code, name=name, college=college, url=absolute_url(href))
