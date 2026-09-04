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

from .config import SCHEMA_VERSION
from .http import Fetcher, SiteUnavailable
from .models import ClassGroup, Course, Department, Semester
from .output import (
    read_semester_times,
    read_syllabus_state,
    write_outputs,
    write_semester_failure,
    write_syllabus,
    write_syllabus_index,
)
from .parse_course import parse_courses
from .parse_dept import parse_class_groups, parse_colleges
from .parse_semester import parse_semesters
from .parse_syllabus import parse_syllabus

log = logging.getLogger("crawler")

#: 首頁,唯一能問出「現在有哪些學年期」的地方
HOME_PAGE = "course.jsp"

#: 非最新學期預設多久重抓一次(小時)。過去的學期資料幾乎不再變動,
#: 每 4 小時全部重抓一次只是白白增加學校的負擔。
DEFAULT_REFRESH_AFTER = 24.0

#: 連續幾個學期「整個抓不到」就中止本批。
#:
#: 實測遇過:2026-09-03 18:00-18:18 UTC 這 18 分鐘 GitHub runner 完全連不到
#: 學校(TCP connect timeout,本機同時是通的),一批 12 個學期全部失敗,
#: 花了 11.5 分鐘在對一台連不上的機器反覆重試。單一學期失敗可能只是那頁有問題,
#: 但連續失敗幾乎一定是對方整體不可用 —— 這時候繼續試沒有意義,也不禮貌。
#: 中止不影響已抓好的學期(它們早就落地了),失敗的下次執行會自動重試。
CONSECUTIVE_FAILURE_LIMIT = 3

#: 教學大綱預設多久重抓一次(小時)。
#:
#: 大綱是老師開學前填好就很少再動的東西(樣本的「最後更新時間」是開學前一個月),
#: 但整學期完全不重抓也不對 —— 有人會補、會改。30 天是個折衷。
DEFAULT_SYLLABUS_REFRESH_AFTER = 720.0

#: 一次執行最多抓幾頁教學大綱。
#:
#: 大綱是**一門課一頁**,115-1 有 2,717 門 —— 以 1 秒的延遲下限計算,
#: 全抓一輪要 45 分鐘。分批抓,幾天內輪完一圈,對學校溫和得多,
#: 也不會讓單一 job 拖太久。0 代表不限。
DEFAULT_MAX_SYLLABUS = 800


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


def parse_year_range(text: str) -> tuple[int, int]:
    """把 `--years` 的值解析成 (最舊, 最新) 學年度。

    接受 `90-114`(範圍)或 `113`(單一學年度)。
    """
    text = text.strip()
    if "-" in text:
        low, _, high = text.partition("-")
    else:
        low = high = text
    try:
        start, end = int(low), int(high)
    except ValueError:
        raise ValueError(f"--years 看不懂:{text!r},格式應為 90-114 或 113") from None
    if start > end:
        start, end = end, start
    return start, end


def backfill_semesters(
    years: tuple[int, int],
    out_dir: Path,
    *,
    force_all: bool = False,
    limit: int | None = None,
) -> list[tuple[Semester, str]]:
    """回補:列出指定學年度範圍內、還沒抓過的學期,由新到舊。

    首頁只掛最近兩個學期,但 `Subj.jsp?format=-2&year=&sem=` 不理會首頁 ——
    實測 90 學年度起的資料都還在,而且版面與現在完全相同(23 欄、教師與
    教室一樣是帶 code 的連結),現有解析器直接吃得下。

    **已經有完整資料的學期永久跳過**,不看新舊。過去的學期不會再變動,
    重抓沒有意義;而且這讓回補可以分批跑,中途失敗再跑一次就會接續下去。
    `--all-semesters` 可以強制重抓。
    """
    known = read_semester_times(out_dir)
    start, end = years
    picked: list[tuple[Semester, str]] = []

    for year in range(end, start - 1, -1):
        for sem in (2, 1):  # 同一學年度裡第 2 學期比較新
            if not force_all and (year, sem) in known:
                continue
            picked.append((Semester(year=year, sem=sem), "回補"))
            if limit is not None and len(picked) >= limit:
                log.info("已達 --max-semesters 上限 %d,其餘留給下一批", limit)
                return picked

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

    if fetcher.unavailable:
        # 斷路器在這個學期抓到一半跳開了。後面那些單位的「0 門課」是「放棄去問」,
        # 不是「真的沒開課」—— 把這種半套結果寫出去會蓋掉線上完整的資料,
        # 而且 meta.json 會記成「這學期剛更新過」,擋住之後 24 小時的重試。
        # 當成整個學期失敗往上拋,由呼叫端記進 errors.json 並中止本批。
        raise SiteUnavailable(
            f"{result.semester} 抓到一半站台就不可用了"
            f"(已完成 {result.ok_departments}/{len(departments)} 個單位),不輸出半套資料"
        )

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
# 教學大綱
# --------------------------------------------------------------------------
def select_syllabus_targets(
    courses: list[Course],
    fetched: dict[str, str],
    *,
    limit: int | None,
    refresh_after: float,
    now: datetime | None = None,
) -> list[Course]:
    """挑這次要抓大綱的課:沒抓過的優先,其次是最久沒重抓的。

    沒有 `syllabus_url` 的課直接跳過 —— 跨校選課那類課程在北科的系統裡
    沒有大綱連結(115-1 一次多的那 265 門就是),硬湊一個 URL 只會白打一頁。

    分批的意義在於:全校一輪要 45 分鐘,每天抓一批、幾天輪完一圈,
    對學校溫和得多。排序保證每一門最終都會輪到,不會有課永遠排不到。
    """
    now = now or datetime.now(timezone.utc)
    stale: list[tuple[float, Course]] = []

    for course in courses:
        if not course.syllabus_url:
            continue
        raw = fetched.get(course.id)
        if raw is None:
            stale.append((float("inf"), course))  # 沒抓過的排最前面
            continue
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            stale.append((float("inf"), course))
            continue
        age = (now - stamp).total_seconds() / 3600
        if age >= refresh_after:
            stale.append((age, course))

    # 越久沒抓的越前面;同齡時用課號排序,讓結果可預測
    stale.sort(key=lambda pair: (-pair[0], pair[1].id))
    picked = [course for _, course in stale]
    if limit:
        picked = picked[:limit]
    return picked


def crawl_syllabi(
    fetcher: Fetcher,
    result: CrawlResult,
    out_dir: Path,
    *,
    limit: int | None,
    refresh_after: float,
    pretty: bool = False,
) -> int:
    """抓一個學期的教學大綱,回傳這次成功抓了幾門。

    **每抓一門就落地。** 一批 800 頁要十幾分鐘,中途失敗時已經抓好的
    不該跟著賠掉 —— 下次執行會從沒抓過的那些接著抓。
    """
    state = read_syllabus_state(out_dir)
    fetched = dict(state.get(result.semester) or {})
    targets = select_syllabus_targets(
        result.courses, fetched, limit=limit, refresh_after=refresh_after
    )
    have = sum(1 for c in result.courses if c.syllabus_url)
    log.info(
        "%s 教學大綱:%d 門有連結,已抓過 %d 門,本次要抓 %d 門",
        result.semester,
        have,
        len(fetched),
        len(targets),
    )
    if not targets:
        return 0

    ok = 0
    for index, course in enumerate(targets, start=1):
        if index % 100 == 0:
            log.info("教學大綱 [%d/%d]", index, len(targets))
        try:
            html = fetcher.fetch(course.syllabus_url)
        except Exception as exc:
            log.error("課程 %s (%s) 的大綱抓取失敗:%s", course.name_zh, course.id, exc)
            result.errors.append(
                {
                    "stage": "syllabus",
                    "course_id": course.id,
                    "course_name": course.name_zh,
                    "url": course.syllabus_url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if isinstance(exc, SiteUnavailable):
                log.error("站台已判定不可用,停止抓大綱;已抓好的保留")
                break
            continue

        parsed = parse_syllabus(html)
        if not parsed:
            # 老師沒填大綱。記下時間戳,不然每次執行都會再來問一次同一頁。
            log.debug("課程 %s 沒有大綱內容", course.id)

        payload = {
            "schema_version": SCHEMA_VERSION,
            "year": result.year,
            "sem": result.sem,
            "course_id": course.id,
            "course_name": course.name_zh,
            "teachers": list(course.teachers),
            "department_ids": list(course.department_ids),
            "url": course.syllabus_url,
            "fetched_at": _utc_now(),
            "has_content": bool(parsed),
            **parsed,
        }
        write_syllabus(out_dir, result.semester, course.id, payload, pretty=pretty)
        fetched[course.id] = payload["fetched_at"]
        ok += 1

    state[result.semester] = fetched
    totals = {result.semester: {"course_count": len(result.courses), "with_url": have}}
    write_syllabus_index(out_dir, state, totals, pretty=pretty)
    log.info("%s 教學大綱本次抓了 %d 門", result.semester, ok)
    return ok


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


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
        "--years",
        metavar="FROM-TO",
        help="回補模式:抓這個學年度範圍內尚未抓過的學期,例 --years 90-114",
    )
    parser.add_argument(
        "--max-semesters",
        type=int,
        default=None,
        metavar="N",
        help="這次最多抓幾個學期(回補分批用,避開 Actions 單 job 6 小時上限)",
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
        "--with-syllabus",
        action="store_true",
        help="順便抓教學大綱(一門課一頁,很慢,預設關閉)",
    )
    parser.add_argument(
        "--max-syllabus",
        type=int,
        default=DEFAULT_MAX_SYLLABUS,
        metavar="N",
        help=f"這次最多抓幾頁教學大綱(預設 {DEFAULT_MAX_SYLLABUS},0 = 不限)",
    )
    parser.add_argument(
        "--syllabus-refresh-after",
        type=float,
        default=DEFAULT_SYLLABUS_REFRESH_AFTER,
        metavar="HOURS",
        help=f"教學大綱隔多久重抓一次(預設 {DEFAULT_SYLLABUS_REFRESH_AFTER:.0f} 小時)",
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
    if args.years and args.year is not None:
        parser.error("--years(回補範圍)與 --year/--sem(單一學期)只能擇一")
    if args.years:
        try:
            parse_year_range(args.years)
        except ValueError as exc:
            parser.error(str(exc))

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
    failed: list[Semester] = []
    consecutive_failures = 0

    for semester, reason in targets:
        log.info("=== 開始抓取 %s(%s)===", semester.path, reason)
        try:
            result = crawl(
                fetcher, semester.year, semester.sem, only_departments=args.dept
            )
        except Exception as exc:
            # 學期層級的容錯。總覽頁抓不到(學校維護、連線逾時)時,原本會
            # 一路往上炸掉整個執行 —— 一批 12 個學期跑到第 8 個掛掉,
            # 前 7 個的成果就因為步驟失敗而不會被發布。記錄後換下一個學期。
            log.error("%s 整個學期抓取失敗:%s", semester.path, exc)
            write_semester_failure(
                args.out, semester.year, semester.sem, exc, pretty=args.pretty
            )
            failed.append(semester)

            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                log.error(
                    "連續 %d 個學期整個抓不到,判定學校端目前不可用,中止本批。"
                    "已抓好的學期不受影響,失敗的下次執行會自動重試。",
                    consecutive_failures,
                )
                break
            continue

        consecutive_failures = 0

        if not result.departments:
            # 太舊的學年期總覽頁是空的(實測 80-1 以前)。寫出去只會多一個
            # 空目錄,還會在 meta.json 留下「抓過了」的紀錄擋住之後的重試。
            log.warning("%s 沒有任何單位,略過不輸出", semester.path)
            continue
        # 每抓完一個學期就落地,後面的學期失敗也不會賠掉前面的成果
        write_outputs(result, args.out, pretty=args.pretty)
        results.append(result)

        if args.with_syllabus:
            if result.partial:
                log.warning("--dept 的局部抓取不抓教學大綱")
            else:
                crawl_syllabi(
                    fetcher,
                    result,
                    args.out,
                    limit=args.max_syllabus or None,
                    refresh_after=args.syllabus_refresh_after,
                    pretty=args.pretty,
                )
                # 大綱抓完可能新增了錯誤,重寫一次 errors.json
                write_outputs(result, args.out, pretty=args.pretty)

    _print_summary(results, fetcher, args.out)

    if failed:
        log.warning(
            "%d 個學期整個抓不到:%s(已記進 errors.json,下次執行會重試)",
            len(failed),
            ", ".join(s.path for s in failed),
        )

    # 只有「某個學期的所有單位都失敗」才算整體失敗
    for result in results:
        if result.departments and result.ok_departments == 0:
            log.error("%s 的所有單位都抓取失敗", result.semester)
            return 1

    # 一個學期都沒抓成功才算整體失敗;有部分成果就要讓它發布出去
    if failed and not results:
        log.error("所有指定的學期都抓取失敗")
        return 1
    return 0


def _targets(args: argparse.Namespace, fetcher: Fetcher) -> list[tuple[Semester, str]]:
    if args.years:
        targets = backfill_semesters(
            parse_year_range(args.years),
            args.out,
            force_all=args.all_semesters,
            limit=args.max_semesters,
        )
        log.info(
            "回補 %s:本次要抓 %d 個學期:%s",
            args.years,
            len(targets),
            ", ".join(s.path for s, _ in targets) or "(無,都抓過了)",
        )
        return targets

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
