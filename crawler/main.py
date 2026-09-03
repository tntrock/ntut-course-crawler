"""CLI entry point:抓取 → 去重 → 寫出靜態 JSON API。

用法:
    python -m crawler.main --year 115 --sem 1 --out data/
    python -m crawler.main --year 115 --sem 1 --out data/ --no-cache --delay 1.5
    python -m crawler.main --year 115 --sem 1 --out data/ --dept 59   # 開發用
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BASE_URL, SCHEMA_VERSION
from .http import Fetcher
from .models import ClassGroup, Course, Department, requirement_table
from .parse_course import parse_courses
from .parse_dept import parse_class_groups, parse_colleges
from .periods import period_table

log = logging.getLogger("crawler")

SOURCE_NAME = "國立臺北科技大學 課程查詢系統"
DISCLAIMER = (
    "本資料由非官方爬蟲自動蒐集,僅供參考,"
    "一切以學校公告與課程系統當下顯示的內容為準。"
)


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
    elapsed: float = 0.0

    @property
    def semester(self) -> str:
        return f"{self.year}-{self.sem}"


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
    result = CrawlResult(year=year, sem=sem)
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
# 輸出
# --------------------------------------------------------------------------
def write_outputs(result: CrawlResult, out_dir: Path, *, pretty: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    semester_dir = out_dir / result.semester
    semester_dir.mkdir(parents=True, exist_ok=True)

    _write_departments(result, semester_dir, pretty)
    _write_courses(result, semester_dir, pretty)
    _write_index(result, out_dir, pretty)
    _write_meta(result, out_dir, pretty)
    _write_errors(result, out_dir, pretty)


def _write_json(path: Path, payload: dict[str, Any], pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("既有的 %s 無法讀取(%s),將整個重建", path.name, exc)
        return None


def _write_departments(result: CrawlResult, semester_dir: Path, pretty: bool) -> None:
    """學院 / 系所 / 班級三層對照。"""
    course_counts: dict[str, int] = {}
    for course in result.courses:
        for dept_id in course.department_ids:
            course_counts[dept_id] = course_counts.get(dept_id, 0) + 1

    departments = []
    for dept in result.departments:
        payload = dept.to_dict()
        payload["class_groups"] = [
            {"id": g.id, "name": g.name, "url": g.url}
            for g in result.class_groups.get(dept.id, [])
        ]
        payload["course_count"] = course_counts.get(dept.id, 0)
        departments.append(payload)

    _write_json(
        semester_dir / "departments.json",
        {
            "schema_version": SCHEMA_VERSION,
            "year": result.year,
            "sem": result.sem,
            "generated_at": _now(),
            "departments": departments,
        },
        pretty,
    )


def _write_courses(result: CrawlResult, semester_dir: Path, pretty: bool) -> None:
    """每個系所一個檔,檔名用系所代碼(中文檔名在 Pages 上要 percent-encoding)。"""
    by_dept: dict[str, list[Course]] = {d.id: [] for d in result.departments}
    for course in result.courses:
        for dept_id in course.department_ids:
            # 合開課程會同時寫進每個開課系所的檔案,讓每個檔都是自足的
            by_dept.setdefault(dept_id, []).append(course)

    names = {d.id: d for d in result.departments}
    for dept_id, courses in by_dept.items():
        dept = names.get(dept_id)
        _write_json(
            semester_dir / "courses" / f"{dept_id}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "year": result.year,
                "sem": result.sem,
                "generated_at": _now(),
                "department": dept.to_dict() if dept else {"id": dept_id},
                "courses": [c.to_dict() for c in courses],
            },
            pretty,
        )


def _index_entry(course: Course, year: int, sem: int) -> dict[str, Any]:
    """index.json 只放搜尋需要的欄位,細節留在各系所檔。"""
    return {
        "id": course.id,
        "name_zh": course.name_zh,
        "teachers": list(course.teachers),
        "time_slots": [s.to_dict() for s in course.time_slots],
        "department_ids": list(course.department_ids),
        "credits": course.credits,
        "year": year,
        "sem": sem,
    }


def _write_index(result: CrawlResult, out_dir: Path, pretty: bool) -> None:
    """輕量索引:前端一次載入就能做關鍵字搜尋,不必逐系所請求。

    同一個 out_dir 裡若已有其他學期的索引,會保留;只替換本次學年期的部分。
    """
    path = out_dir / "index.json"
    existing = _read_json(path) or {}
    kept = [
        entry
        for entry in existing.get("courses", [])
        if (entry.get("year"), entry.get("sem")) != (result.year, result.sem)
    ]
    courses = kept + [_index_entry(c, result.year, result.sem) for c in result.courses]

    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "course_count": len(courses),
            "courses": courses,
        },
        pretty,
    )


def _write_meta(result: CrawlResult, out_dir: Path, pretty: bool) -> None:
    path = out_dir / "meta.json"
    existing = _read_json(path) or {}
    semesters = [
        s
        for s in existing.get("semesters", [])
        if (s.get("year"), s.get("sem")) != (result.year, result.sem)
    ]
    semesters.append(
        {
            "year": result.year,
            "sem": result.sem,
            "path": result.semester,
            "generated_at": _now(),
            "department_count": len(result.departments),
            "class_group_count": sum(len(g) for g in result.class_groups.values()),
            "course_count": len(result.courses),
            "merged_course_count": result.merged_courses,
            "failed_department_count": result.failed_departments,
        }
    )
    semesters.sort(key=lambda s: (s.get("year", 0), s.get("sem", 0)), reverse=True)

    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "source": {"name": SOURCE_NAME, "url": BASE_URL},
            "disclaimer": DISCLAIMER,
            "semesters": semesters,
            "periods": period_table(),
            "requirement_symbols": requirement_table(),
        },
        pretty,
    )


def _write_errors(result: CrawlResult, out_dir: Path, pretty: bool) -> None:
    """沒有錯誤時也要寫,不然使用者會看到上一輪殘留的錯誤檔。"""
    _write_json(
        out_dir / "errors.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "year": result.year,
            "sem": result.sem,
            "errors": result.errors,
        },
        pretty,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler.main",
        description="爬取北科大課程系統並輸出靜態 JSON API。",
    )
    parser.add_argument("--year", type=int, required=True, help="學年度,例 115")
    parser.add_argument("--sem", type=int, required=True, help="學期,1 或 2")
    parser.add_argument("--out", type=Path, default=Path("data"), help="輸出目錄")
    parser.add_argument(
        "--dept",
        action="append",
        metavar="CODE",
        help="只抓指定系所代碼(可重複),開發用。例:--dept 59",
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
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    fetcher = Fetcher(delay=args.delay, use_cache=not args.no_cache)
    log.info("延遲 %.2fs / 快取 %s", fetcher.delay, "關閉" if args.no_cache else "開啟")

    result = crawl(fetcher, args.year, args.sem, only_departments=args.dept)
    write_outputs(result, args.out, pretty=args.pretty)

    _print_summary(result, fetcher, args.out)

    # 只有「全部單位都失敗」才算整體失敗
    if result.departments and result.ok_departments == 0:
        log.error("所有單位都抓取失敗")
        return 1
    return 0


def _print_summary(result: CrawlResult, fetcher: Fetcher, out_dir: Path) -> None:
    index_path = out_dir / "index.json"
    index_size = index_path.stat().st_size / 1024 if index_path.is_file() else 0

    print()
    print(f"學年期        {result.semester}")
    print(f"單位          成功 {result.ok_departments} / 失敗 {result.failed_departments}")
    print(f"班級          {sum(len(g) for g in result.class_groups.values())}")
    print(f"課程(去重後)  {len(result.courses)}(合併掉 {result.merged_courses} 筆重複課號)")
    print(f"請求 / 快取   {fetcher.request_count} / {fetcher.cache_hit_count}")
    print(f"錯誤          {len(result.errors)}")
    print(f"index.json    {index_size:.0f} KB")
    print(f"耗時          {result.elapsed:.1f}s")
    print(f"輸出          {out_dir.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
