"""CLI entry point:抓取 → 去重 → 寫出靜態 JSON API。

用法:
    python -m crawler.main --out data/                  # 自動偵測學年期(預設)
    python -m crawler.main --year 115 --sem 1 --out data/
    python -m crawler.main --out data/ --dept 59 --pretty   # 開發用

學年期不寫死
------------
不給 `--year/--sem` 時會先讀課程系統首頁,看學校現在掛了哪幾個「上課時間表」,
再決定要抓哪些。115-1 過完換 115-2、再換 116-1,程式與 workflow 都不用改。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .http import Fetcher
from .models import ClassGroup, Course, Department, Semester
from .output import read_semester_times, write_outputs
from .parse_course import parse_courses
from .parse_dept import parse_class_groups, parse_colleges
from .parse_semester import parse_semesters

log = logging.getLogger("crawler")

#: 首頁,唯一能問出「現在有哪些學年期」的地方
HOME_PAGE = "course.jsp"

#: 非最新學期預設多久重抓一次(小時)。過去的學期資料幾乎不再變動,
#: 每 4 小時全部重抓一次只是白白增加學校的負擔。
DEFAULT_REFRESH_AFTER = 24.0


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
@dataclass
class CrawlResult:
    year: int
    sem: int
    departments: list[Department] = field(default_factory=list)
    class_groups: dict[str, list[ClassGroup]] = field(default_factory=dict)
    courses: list[Course] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    ok_departments: int = 0
    failed_departments: int = 0
    merged_courses: int = 0  # 同課號在多個班級頁重複出現、被合併掉的次數
    partial: bool = False  # --dept 只抓了部分單位,資料集不完整
    elapsed: float = 0.0

    @property
    def semester(self) -> str:
        return f"{self.year}-{self.sem}"


def discover_semesters(fetcher: Fetcher) -> list[Semester]:
    """問學校首頁現在有哪些學年期,新到舊排序。只花一次請求。"""
    html = fetcher.fetch(HOME_PAGE)
    semesters = parse_semesters(html)
    log.info(
        "首頁列出 %d 個學年期:%s",
        len(semesters),
        ", ".join(s.path for s in semesters) or "(無)",
    )
    return semesters


def select_semesters(
    available: list[Semester],
    out_dir: Path,
    *,
    refresh_after: float = DEFAULT_REFRESH_AFTER,
    force_all: bool = False,
    now: datetime | None = None,
) -> list[tuple[Semester, str]]:
    """從可抓的學年期裡挑出這次真的要抓的,並附上原因。

    規則:
    - **最新的學期一定抓** —— 選課期間資料每天在動,那才是大家要看的。
    - 還沒有資料的學期抓 —— 新學期一掛出來就立刻補上。
    - 資料超過 `refresh_after` 小時沒更新的抓 —— 讓舊學期偶爾也對一次,
      順便修掉先前抓壞的部分。
    - 其餘跳過。

    首頁已經下架、但本地還有資料的學期不會出現在這裡,也不會被刪 —— 歷史資料留著。
    """
    if not available:
        return []

    now = now or datetime.now(timezone.utc)
    known = read_semester_times(out_dir)
    newest = available[0]
    picked: list[tuple[Semester, str]] = []

    for semester in available:
        key = (semester.year, semester.sem)
        stamp = known.get(key)

        if force_all:
            reason = "--all-semesters"
        elif semester == newest:
            reason = "最新學期"
        elif stamp is None:
            reason = "尚無資料"
        else:
            age = (now - stamp).total_seconds() / 3600
            if age >= refresh_after:
                reason = f"資料已 {age:.1f} 小時未更新"
            else:
                log.info("略過 %s:資料 %.1f 小時前才更新過", semester.path, age)
                continue

        picked.append((semester, reason))

    return picked


def crawl(
    fetcher: Fetcher,
    year: int,
    sem: int,
    *,
    only_departments: list[str] | None = None,
) -> CrawlResult:
    """跑完整條 format=-2 → -3 → -4 的抓取流程。

    單一系所失敗只記錄後繼續,不會拖垮整批(plan.md §3 Phase 4)。
    """
    started = time.monotonic()
    result = CrawlResult(year=year, sem=sem, partial=bool(only_departments))
    params = {"year": year, "sem": sem}

    overview = fetcher.fetch("Subj.jsp", params={"format": -2, **params})
    departments = parse_colleges(overview)
    log.info("總覽頁解析到 %d 個單位", len(departments))

    if only_departments:
        wanted = set(only_departments)
        missing = wanted - {d.id for d in departments}
        if missing:
            log.warning("--dept 指定的代碼不存在:%s", ", ".join(sorted(missing)))
        departments = [d for d in departments if d.id in wanted]
        log.warning("只抓取 %d 個指定單位,輸出將是不完整的資料集", len(departments))

    result.departments = departments
    # 課號 → Course。合開課程會出現在多個班級頁,在這裡即時合併。
    merged: dict[str, Course] = {}

    for index, dept in enumerate(departments, start=1):
        log.info("[%d/%d] %s (%s)", index, len(departments), dept.name, dept.id)
        try:
            groups = _crawl_department(fetcher, dept, params, merged, result)
        except Exception as exc:  # 單位層級失敗:記錄後換下一個
            log.error("單位 %s (%s) 抓取失敗:%s", dept.name, dept.id, exc)
            result.errors.append(
                {
                    "stage": "department",
                    "department_id": dept.id,
                    "department_name": dept.name,
                    "url": dept.url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            result.failed_departments += 1
            continue

        result.class_groups[dept.id] = groups
        result.ok_departments += 1

    result.courses = sorted(merged.values(), key=lambda c: c.id)
    result.elapsed = time.monotonic() - started
    return result


def _crawl_department(
    fetcher: Fetcher,
    dept: Department,
    params: dict[str, Any],
    merged: dict[str, Course],
    result: CrawlResult,
) -> list[ClassGroup]:
    html = fetcher.fetch("Subj.jsp", params={"format": -3, "code": dept.id, **params})
    groups = parse_class_groups(html, dept.id)

    for group in groups:
        try:
            page = fetcher.fetch(
                "Subj.jsp", params={"format": -4, "code": group.id, **params}
            )
        except Exception as exc:
            # 班級層級失敗不影響同單位的其他班級
            log.error("班級 %s (%s) 抓取失敗:%s", group.name, group.id, exc)
            result.errors.append(
                {
                    "stage": "class_group",
                    "department_id": dept.id,
                    "department_name": dept.name,
                    "class_group_id": group.id,
                    "class_group_name": group.name,
                    "url": group.url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for course in parse_courses(page):
            course.class_ids = [group.id]
            course.department_ids = [dept.id]
            if not course.classes:
                # 表格上方的班級標題偶爾抓不到,退回用連結上的班級名稱
                course.classes = [group.name]

            existing = merged.get(course.id)
            if existing is None:
                merged[course.id] = course
            else:
                # 這條路徑很常走:115-1 全校實測合併掉 310 筆重複課號,
                # 其中 110 門課跨多個系所(通識、體育、校院級課程等會同時
                # 出現在多個班級頁)。注意「合開」是另一回事 —— 合開課程在
                # 各班級頁多半是**不同課號**(數位影像處理在資工四是 364893、
                # 在資工所是 364899),那種情況不會在這裡合併。
                existing.merge_from(course)
                result.merged_courses += 1

    return groups


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler.main",
        description="爬取北科大課程系統並輸出靜態 JSON API。",
    )
    parser.add_argument(
        "--year", type=int, help="學年度,例 115。不給就自動偵測(要跟 --sem 成對)"
    )
    parser.add_argument("--sem", type=int, help="學期,1 或 2。不給就自動偵測")
    parser.add_argument("--out", type=Path, default=Path("data"), help="輸出目錄")
    parser.add_argument(
        "--dept",
        action="append",
        metavar="CODE",
        help="只抓指定系所代碼(可重複),開發用。例:--dept 59",
    )
    parser.add_argument(
        "--refresh-after",
        type=float,
        default=DEFAULT_REFRESH_AFTER,
        metavar="HOURS",
        help=f"非最新學期隔多久重抓一次(預設 {DEFAULT_REFRESH_AFTER:.0f} 小時)",
    )
    parser.add_argument(
        "--all-semesters",
        action="store_true",
        help="首頁列出的每個學年期都重抓,忽略 --refresh-after",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="每次請求後的延遲秒數(下限 0.5,預設 1.0)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="略過 .cache/,強制重新抓取"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="輸出縮排過的 JSON(檔案會變大)"
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if (args.year is None) != (args.sem is None):
        parser.error("--year 與 --sem 要嘛一起給,要嘛都不給(自動偵測)")

    fetcher = Fetcher(delay=args.delay, use_cache=not args.no_cache)
    log.info("延遲 %.2fs / 快取 %s", fetcher.delay, "關閉" if args.no_cache else "開啟")

    try:
        targets = _targets(args, fetcher)
    except Exception as exc:
        log.error("無法決定要抓哪些學年期:%s", exc)
        return 1

    if not targets:
        log.warning("沒有需要更新的學年期,結束")
        return 0

    results: list[CrawlResult] = []
    for semester, reason in targets:
        log.info("=== 開始抓取 %s(%s)===", semester.path, reason)
        result = crawl(
            fetcher, semester.year, semester.sem, only_departments=args.dept
        )
        # 每抓完一個學期就落地,後面的學期失敗也不會賠掉前面的成果
        write_outputs(result, args.out, pretty=args.pretty)
        results.append(result)

    _print_summary(results, fetcher, args.out)

    # 只有「某個學期的所有單位都失敗」才算整體失敗
    for result in results:
        if result.departments and result.ok_departments == 0:
            log.error("%s 的所有單位都抓取失敗", result.semester)
            return 1
    return 0


def _targets(args: argparse.Namespace, fetcher: Fetcher) -> list[tuple[Semester, str]]:
    if args.year is not None:
        return [(Semester(year=args.year, sem=args.sem), "命令列指定")]

    available = discover_semesters(fetcher)
    if not available:
        raise RuntimeError("首頁沒有任何上課時間表連結")

    targets = select_semesters(
        available,
        args.out,
        refresh_after=args.refresh_after,
        force_all=args.all_semesters,
    )
    log.info(
        "本次要抓 %d 個學年期:%s",
        len(targets),
        ", ".join(f"{s.path}({r})" for s, r in targets) or "(無)",
    )
    return targets


def _print_summary(
    results: list[CrawlResult], fetcher: Fetcher, out_dir: Path
) -> None:
    index_path = out_dir / "index.json"
    index_size = index_path.stat().st_size / 1024 if index_path.is_file() else 0

    print()
    for result in results:
        print(f"學年期        {result.semester}")
        print(
            f"單位          成功 {result.ok_departments} / "
            f"失敗 {result.failed_departments}"
        )
        print(f"班級          {sum(len(g) for g in result.class_groups.values())}")
        print(
            f"課程(去重後)  {len(result.courses)}"
            f"(合併掉 {result.merged_courses} 筆重複課號)"
        )
        print(f"錯誤          {len(result.errors)}")
        print(f"耗時          {result.elapsed:.1f}s")
        print()

    print(f"請求 / 快取   {fetcher.request_count} / {fetcher.cache_hit_count}")
    print(f"index.json    {index_size:.0f} KB")
    print(f"輸出          {out_dir.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
