"""偵察:首頁沒列出的舊學年期,URL 是否仍然可以直接存取?

背景:`course.jsp` 首頁每次只掛最近兩個學期的「上課時間表」入口
(目前是 115-1 與 114-2),所以 `parse_semester.py` 只看得到這兩個。
但 `Subj.jsp` 是老式 JSP,很可能直接吃 query string 查資料庫,
首頁沒放連結不代表擋掉 —— 這件事只能實測。

做法(依 plan.md §5.3「一次只抓一頁並說明理由」,延遲拉到 2 秒):
  第一輪  對數個學年期各抓一次 format=-2 總覽頁,看解析得到幾個單位
  第二輪  對最舊的可用學年期往下鑽 format=-3 / -4,確認整棵樹都在,
          而不是只有總覽頁還活著

判讀:
  units > 0        → 這個學年期可抓
  units == 0       → 頁面回了但沒有資料(通常是空白頁或錯誤頁)
  例外              → 連不上 / 4xx / 5xx

用法:
    python -m scripts.recon4
"""

from __future__ import annotations

import logging
import sys

from crawler.http import Fetcher
from crawler.parse_course import parse_courses
from crawler.parse_dept import parse_class_groups, parse_colleges
from crawler.parse_util import clean, soup_of

#: 由新到舊粗掃。先確認邊界大概在哪,不要一開始就一年一年掃。
PROBE_YEARS = [114, 113, 110, 105, 95]
SEM = 1

DELAY = 2.0  # 偵察一律放慢


def probe_overview(fetcher: Fetcher, year: int, sem: int) -> int | None:
    """回傳總覽頁解析到的單位數;連不上回 None。"""
    try:
        html = fetcher.fetch("Subj.jsp", params={"format": -2, "year": year, "sem": sem})
    except Exception as exc:
        print(f"  {year}-{sem}  ✗ {type(exc).__name__}: {exc}")
        return None

    units = parse_colleges(html)
    title = clean(soup_of(html).title.get_text()) if soup_of(html).title else None
    print(f"  {year}-{sem}  {len(units):>3} 個單位   {len(html):>6} bytes   title={title!r}")
    return len(units)


def main() -> int:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    fetcher = Fetcher(delay=DELAY)

    print("=== 第一輪:總覽頁 format=-2 ===")
    alive: list[int] = []
    for year in PROBE_YEARS:
        count = probe_overview(fetcher, year, SEM)
        if count:
            alive.append(year)

    if not alive:
        print("\n沒有任何舊學年期可用,結束。")
        return 0

    oldest = alive[-1]
    print(f"\n=== 第二輪:往下鑽 {oldest}-{SEM},確認整棵樹都在 ===")
    html = fetcher.fetch(
        "Subj.jsp", params={"format": -2, "year": oldest, "sem": SEM}
    )
    depts = parse_colleges(html)
    dept = next((d for d in depts if d.id == "59"), depts[0])
    print(f"  單位 {dept.id} {dept.name}(學院 {dept.college})")

    page = fetcher.fetch(
        "Subj.jsp", params={"format": -3, "year": oldest, "sem": SEM, "code": dept.id}
    )
    groups = parse_class_groups(page, dept.id)
    print(f"  format=-3 → {len(groups)} 個班級:{', '.join(g.name for g in groups)}")

    if groups:
        group = groups[0]
        page = fetcher.fetch(
            "Subj.jsp",
            params={"format": -4, "year": oldest, "sem": SEM, "code": group.id},
        )
        courses = parse_courses(page)
        print(f"  format=-4 → {group.name} 有 {len(courses)} 門課")
        for course in courses[:3]:
            print(
                f"      {course.id}  {course.name_zh}  "
                f"{course.teachers}  {course.requirement_type}"
            )

    print(f"\n請求 {fetcher.request_count} 次 / 快取命中 {fetcher.cache_hit_count} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
