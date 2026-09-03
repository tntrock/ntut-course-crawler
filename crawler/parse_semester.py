"""解析課程系統首頁(course.jsp),找出目前開放查詢的學年期。

純函式:吃 HTML 字串 → 吐 dataclass,不發任何網路請求。

為什麼需要這支:
    學年期不能寫死。115-1 過完會變 115-2,再過去是 116-1,
    寫死就得每學期手動改一次 workflow —— 那不叫自動化。
    首頁本來就會列出所有「上課時間表」的入口,一個學期一個連結:

        <a href="Subj.jsp?format=-2&year=115&sem=1">115學年度第1學期上課時間表</a>
        <a href="Subj.jsp?format=-2&year=114&sem=2">114學年度第2學期上課時間表</a>

    抓這些連結就等於問學校「你現在有哪幾個學期」,學校自己會回答。

實測(tests/fixtures/course_home_real.html):首頁同時掛著 115-1 與 114-2。
"""

from __future__ import annotations

import logging

from .models import Semester
from .parse_util import query_param, soup_of

log = logging.getLogger(__name__)

#: 上課時間表入口的 script 名稱與 format。首頁上還有學程查詢、教師授課時數表、
#: 教室使用情形等其他連結,它們也帶 year/sem,所以一定要一起比對 format。
_SCRIPT = "Subj.jsp"
_FORMAT = "-2"


def parse_semesters(html: str) -> list[Semester]:
    """回傳首頁列出的所有學年期,**新到舊**排序。

    只認 `Subj.jsp?format=-2` 的連結(上課時間表)。year / sem 解析不出來的
    連結會記 warning 後跳過,不讓一個壞連結拖垮整批。
    """
    soup = soup_of(html)
    found: list[Semester] = []
    seen: set[tuple[int, int]] = set()

    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if _SCRIPT not in href:
            continue
        if query_param(href, "format") != _FORMAT:
            continue

        year = _int(query_param(href, "year"))
        sem = _int(query_param(href, "sem"))
        if year is None or sem is None:
            log.warning("首頁的上課時間表連結缺少 year/sem:%s", href)
            continue

        key = (year, sem)
        if key in seen:
            continue
        seen.add(key)
        found.append(Semester(year=year, sem=sem))

    if not found:
        log.warning("首頁找不到任何上課時間表連結,學校可能改版了")

    # Semester 是 order=True 的 dataclass,直接依 (year, sem) 由新到舊排
    return sorted(found, reverse=True)


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
