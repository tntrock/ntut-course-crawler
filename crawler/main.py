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
import hashlib
import json
import logging
import os
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
    is_frozen_semester,
    read_semester_times,
    syllabus_done_semesters,
    read_syllabus_frozen,
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
#: 排程是一天兩班(台灣 09:00 / 21:00),兩班都要真的全跑一輪,所以這個值
#: 必須小於兩班的間隔。名目間隔是 12 小時,但**不能拿 12 當基準**:
#: Actions 的 cron 只保證「不早於」,這個 repo 實測會遲 2~4 小時,前一班遲
#: 到、後一班準時的話,實際間隔會被壓成 8 小時甚至更短。設 6 小時是為了
#: 讓壓縮後的兩班都還是會抓,同時仍擋得住「排程剛跑完又手動觸發一次」。
#:
#: 大綱不是填完就不動的 —— 2026-09-04 抽驗 50 份,最後更新時間橫跨
#: 2026-06-01 到抓取前兩小時,開學前老師正在大量修改。
DEFAULT_SYLLABUS_REFRESH_AFTER = 6.0

#: 一次執行最多抓幾頁教學大綱。0 代表不限。
#:
#: 大綱是**一門課一頁**。115-1 的 2,717 門課裡有 1,909 門有大綱連結,
#: 實測 1.20 秒/頁 → 全抓一輪約 38 分鐘,每天跑得完,所以預設不限。
#: 想分批(例如冒煙測試)再用 --max-syllabus 壓。
DEFAULT_MAX_SYLLABUS = 0


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
    # 這次是 --years 的回補。回補的對象是已經結束的學期,不該留下「今天的」
    # 人數快照 —— 那個學期的數字早就定案,在時間軸上多一個今天的轉折點
    # 只會誤導。刻意不用「是不是最新學期」來判斷:學校可能在學期還沒結束時
    # 就把下一個學期掛上首頁,那樣會讓當期的人數快照在期中無聲停掉。
    backfill: bool = False
    syllabus_fetched: int = 0  # 這次抓了幾門課的大綱
    syllabus_written: int = 0  # 其中幾門內容真的變了、重寫了檔案
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
    need_syllabus: bool = False,
) -> list[tuple[Semester, str]]:
    """回補:列出指定學年度範圍內、還沒抓過的學期,由新到舊。

    首頁只掛最近兩個學期,但 `Subj.jsp?format=-2&year=&sem=` 不理會首頁 ——
    實測 90 學年度起的資料都還在,而且版面與現在完全相同(23 欄、教師與
    教室一樣是帶 code 的連結),現有解析器直接吃得下。

    **已經有完整資料的學期永久跳過**,不看新舊。過去的學期不會再變動,
    重抓沒有意義;而且這讓回補可以分批跑,中途失敗再跑一次就會接續下去。
    `--all-semesters` 可以強制重抓。

    `need_syllabus`(即 `--with-syllabus`)會多要求一件事:大綱也補完了才算
    抓過。課表已經有、但大綱還沒抓的學期會重新排進來 —— 它必須先重抓一次
    課表(約 355 頁),因為每門課的大綱網址只有課表頁面上有。
    """
    known = read_semester_times(out_dir)
    syllabus_done = syllabus_done_semesters(out_dir) if need_syllabus else set()
    start, end = years
    picked: list[tuple[Semester, str]] = []

    for year in range(end, start - 1, -1):
        for sem in (2, 1):  # 同一學年度裡第 2 學期比較新
            semester = Semester(year=year, sem=sem)
            if not force_all and (year, sem) in known:
                if not need_syllabus or semester.path in syllabus_done:
                    continue
                picked.append((semester, "補大綱"))
                if limit is not None and len(picked) >= limit:
                    log.info("已達 --max-semesters 上限 %d,其餘留給下一批", limit)
                    return picked
                continue
            picked.append((semester, "回補"))
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

    `refresh_after=inf` 代表「只補沒抓過的,抓過的一律不重抓」——
    歷史學期的大綱是凍結的,回補完就不該再打學校一次。
    """
    now = now or datetime.now(timezone.utc)
    stale: list[tuple[float, Course]] = []

    for course in courses:
        if not course.syllabus_url:
            continue
        entry = fetched.get(course.id)
        # schema v2 的狀態是單純的時間字串,v3 起是 {"at": ..., "hash": ...}
        raw = entry.get("at") if isinstance(entry, dict) else entry
        if not isinstance(raw, str):
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


def syllabus_content_hash(payload: dict[str, Any]) -> str:
    """大綱內容的指紋。**不含任何時間戳**,那正是重點。

    v2 的大綱檔帶 `fetched_at`,於是一天兩班、每班 1,909 份大綱,即使老師
    一個字都沒改,git 也會收下 1,909 個新 blob —— gh-pages 就是這樣一天
    長 1.2 MB 的。改成比對內容雜湊,沒變就整個不重寫那個檔,配合
    `keep_files: true`,遠端那份原封不動留著。

    16 個 hex 字(64 bits)對八萬多份文件綽綽有餘,而且比全長 sha256
    省下三分之二的狀態檔體積。
    """
    stable = {k: v for k, v in payload.items() if k != "content_hash"}
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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

    **已經收合的歷史學期整個跳過。** 見 `read_syllabus_frozen`。
    """
    frozen_semesters = read_syllabus_frozen(out_dir)
    if result.semester in frozen_semesters:
        log.info(
            "%s 的大綱已於 %s 補完並收合,跳過",
            result.semester,
            frozen_semesters[result.semester].get("at") or "?",
        )
        return 0

    frozen = is_frozen_semester(result.year, result.sem, out_dir)
    if frozen and refresh_after != float("inf"):
        # 歷史學期的大綱不會再變。沿用當期的 6 小時週期會讓每一班都想重抓
        # 兩萬多頁 —— 那是回補完之後最容易踩到的坑,在這裡一次擋掉。
        log.info("%s 不是最新學期,大綱只補沒抓過的", result.semester)
        refresh_after = float("inf")

    state = read_syllabus_state(out_dir)
    fetched = dict(state.get(result.semester) or {})
    missing_before = sum(
        1 for c in result.courses if c.syllabus_url and c.id not in fetched
    )
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
    # 沒事做就連 syllabus.json 都不要重寫。唯一的例外是「已經補完、但還沒
    # 收合」的歷史學期 —— 例如當期剛換代、上一個學期昨天才變成歷史學期,
    # 這時 targets 本來就是空的,但該收的狀態還留著。
    if not targets and not (frozen and missing_before == 0):
        return 0

    ok = 0
    written = 0
    interrupted = False
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
                interrupted = True
                break
            continue

        parsed = parse_syllabus(html)
        if not parsed:
            # 老師沒填大綱。記下狀態,不然每次執行都會再來問一次同一頁。
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
            "has_content": bool(parsed),
            **parsed,
        }
        digest = syllabus_content_hash(payload)
        payload["content_hash"] = digest

        previous = fetched.get(course.id) or {}
        if digest != previous.get("hash"):
            # 內容變了(或這是第一次抓)才落地。沒變就讓 keep_files 保留
            # 遠端那份 —— 本機根本沒有那個檔,不寫就是不動它。
            write_syllabus(out_dir, result.semester, course.id, payload, pretty=pretty)
            written += 1

        fetched[course.id] = {"at": _utc_now(), "hash": digest}
        ok += 1

    state[result.semester] = fetched
    totals = {result.semester: {"course_count": len(result.courses), "with_url": have}}

    # 收合的條件抓得很嚴:必須是歷史學期、這一輪沒被站台中斷、而且 --max-syllabus
    # 沒有把 targets 砍短(否則「跑完了」只代表跑完這一批,不代表補完了)。
    newly_frozen: dict[str, dict[str, Any]] | None = None
    if frozen and not interrupted and len(targets) >= missing_before:
        remaining = have - len(fetched)
        newly_frozen = {
            result.semester: {
                "fetched": len(fetched),
                "with_url": have,
                "at": _utc_now(),
            }
        }
        if remaining:
            # 補不到的通常是學校那頁本身壞掉,已經記在 errors.json 裡。
            # 為了幾筆固定失敗的課而讓兩千多筆狀態一直留在 syllabus.json,
            # 划不來 —— 記下缺口,要重試就手動把這學期從 frozen 刪掉。
            newly_frozen[result.semester]["missing"] = remaining
            log.warning("%s 有 %d 門大綱始終抓不到,見 errors.json", result.semester, remaining)
        state.pop(result.semester, None)
        log.info("%s 的大綱補完,狀態收合成一筆(%d 門)", result.semester, len(fetched))

    write_syllabus_index(
        out_dir, state, totals, frozen=newly_frozen, pretty=pretty
    )
    result.syllabus_fetched = ok
    result.syllabus_written = written
    log.info(
        "%s 教學大綱本次抓了 %d 門,其中 %d 門內容有變動而重寫",
        result.semester,
        ok,
        written,
    )
    return ok


def write_run_summary(
    path: Path | None,
    started_at: str,
    results: list[CrawlResult],
    failed: list[Semester],
    fetcher: Fetcher,
    *,
    exit_code: int | None = None,
) -> None:
    """把「這次跑了什麼」寫成一個小側寫檔,給 `crawler.runlog` 撿走。

    **每抓完一個學期就重寫一次**,不是等整批結束才寫。job 逾時是直接把
    行程砍掉的,等到最後才寫等於最需要紀錄的那幾次剛好什麼都沒有。

    寫失敗絕對不能影響抓取 —— 這只是紀錄,不是資料。所以整段包在
    try/except 裡,壞了就吞掉。
    """
    if path is None:
        return
    payload = {
        "started_at": started_at,
        "requests": fetcher.request_count,
        "cache_hits": fetcher.cache_hit_count,
        "semesters": [
            {
                "semester": r.semester,
                "courses": len(r.courses),
                "departments_ok": r.ok_departments,
                "departments_failed": r.failed_departments,
                "errors": len(r.errors),
                "seconds": round(r.elapsed, 1),
                "syllabus_fetched": r.syllabus_fetched,
                "syllabus_written": r.syllabus_written,
            }
            for r in results
        ],
        "failed_semesters": [s.path for s in failed],
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("寫不出執行側寫檔 %s:%s(不影響抓取)", path, exc)


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
        help="這次最多抓幾頁教學大綱(預設 0 = 不限,全校一輪約 38 分鐘)",
    )
    parser.add_argument(
        "--syllabus-refresh-after",
        type=float,
        default=DEFAULT_SYLLABUS_REFRESH_AFTER,
        metavar="HOURS",
        help=f"教學大綱隔多久重抓一次(預設 {DEFAULT_SYLLABUS_REFRESH_AFTER:.0f} 小時)",
    )
    parser.add_argument(
        "--run-summary",
        type=Path,
        default=None,
        metavar="PATH",
        help="把這次跑了什麼寫成側寫檔(給 crawler.runlog 記進 runs.json)",
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

    started_at = _utc_now()
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

    def snapshot(exit_code: int | None = None) -> None:
        write_run_summary(
            args.run_summary, started_at, results, failed, fetcher, exit_code=exit_code
        )

    for semester, reason in targets:
        log.info("=== 開始抓取 %s(%s)===", semester.path, reason)
        try:
            result = crawl(
                fetcher, semester.year, semester.sem, only_departments=args.dept
            )
            result.backfill = bool(args.years)
        except Exception as exc:
            # 學期層級的容錯。總覽頁抓不到(學校維護、連線逾時)時,原本會
            # 一路往上炸掉整個執行 —— 一批 12 個學期跑到第 8 個掛掉,
            # 前 7 個的成果就因為步驟失敗而不會被發布。記錄後換下一個學期。
            log.error("%s 整個學期抓取失敗:%s", semester.path, exc)
            write_semester_failure(
                args.out, semester.year, semester.sem, exc, pretty=args.pretty
            )
            failed.append(semester)
            snapshot()

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

        snapshot()

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
            snapshot(1)
            return 1

    # 一個學期都沒抓成功才算整體失敗;有部分成果就要讓它發布出去
    if failed and not results:
        log.error("所有指定的學期都抓取失敗")
        snapshot(1)
        return 1
    snapshot(0)
    return 0


def _targets(args: argparse.Namespace, fetcher: Fetcher) -> list[tuple[Semester, str]]:
    if args.years:
        targets = backfill_semesters(
            parse_year_range(args.years),
            args.out,
            force_all=args.all_semesters,
            limit=args.max_semesters,
            need_syllabus=args.with_syllabus,
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
