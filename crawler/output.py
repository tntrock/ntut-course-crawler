"""把 CrawlResult 寫成靜態 JSON API。

設計原則
--------
1. **每個檔案自足。** 拿到 `teachers/12095.json` 就該看得懂那位老師開了什麼課,
   不必再去載 `index.json` 對照。重複一點資料換來使用端少一次請求,划算。
2. **檔名只用代碼,不用中文。** 中文檔名在 GitHub Pages 上要 percent-encoding,
   使用者得自己處理;而且系所會改名,代碼相對穩定。
3. **多學期共存。** `meta.json` / `index.json` / `errors.json` 是跨學期的,
   寫入時只替換本次學年期的部分,其他學期原封不動保留。
4. **只新增、不改既有欄位。** 這裡每加一個端點都是 additive change,
   `SCHEMA_VERSION` 維持不變(見 README 的相容性承諾)。

清單檔(`teachers.json` / `classes.json` …)只放「有哪些」與筆數,
明細檔才放完整課程物件 —— 前端做下拉選單時不必先吞下整包資料。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .config import BASE_URL, INDEX_SEMESTERS, SCHEMA_VERSION
from .models import Course, requirement_table
from .periods import period_table

if TYPE_CHECKING:  # 避免與 main.py 互相 import
    from .main import CrawlResult

log = logging.getLogger("crawler.output")

SOURCE_NAME = "國立臺北科技大學 課程查詢系統"
DISCLAIMER = (
    "本資料由非官方爬蟲自動蒐集,僅供參考,"
    "一切以學校公告與課程系統當下顯示的內容為準。"
)

#: 可以直接當檔名用的代碼。學校的代碼都是數字,但這些字串來自爬回來的 HTML,
#: 一律先驗證再拿去組路徑,免得哪天頁面壞掉就寫出 `../../etc` 這種檔名。
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: 每次完整抓取都會整個重建的子目錄。不先清掉的話,
#: 上一輪留下的班級 / 教師檔會變成永遠不會更新的幽靈資料。
_REBUILT_SUBDIRS = ("courses", "teachers", "classes")


# --------------------------------------------------------------------------
# 對外入口
# --------------------------------------------------------------------------
def write_outputs(result: "CrawlResult", out_dir: Path, *, pretty: bool = False) -> None:
    """寫出一個學年期的全部檔案,並更新跨學期的索引。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    semester_dir = out_dir / result.semester
    semester_dir.mkdir(parents=True, exist_ok=True)

    if not result.partial:
        _clean_rebuilt_dirs(semester_dir)
    else:
        log.warning("這是 --dept 的局部抓取,不清除舊檔,輸出將是新舊混合的資料集")

    # 本學期
    _write_departments(result, semester_dir, pretty)
    _write_courses(result, semester_dir, pretty)
    _write_teachers(result, semester_dir, pretty)
    _write_classes(result, semester_dir, pretty)
    _write_programs(result, semester_dir, pretty)
    _write_classrooms(result, semester_dir, pretty)
    _write_schedule(result, semester_dir, pretty)
    _write_semester_index(result, semester_dir, pretty)

    # 跨學期
    _write_index(result, out_dir, pretty)
    _write_meta(result, out_dir, pretty)
    _write_errors(result, out_dir, pretty)


def _clean_rebuilt_dirs(semester_dir: Path) -> None:
    for name in _REBUILT_SUBDIRS:
        target = semester_dir / name
        if target.is_dir():
            shutil.rmtree(target)


# --------------------------------------------------------------------------
# 共用小工具
# --------------------------------------------------------------------------
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


def _safe_id(value: str | None, what: str) -> str | None:
    """代碼能不能拿來當檔名。不能的話回 None,由呼叫端決定怎麼降級。"""
    if value and _SAFE_ID_RE.match(value):
        return value
    if value:
        log.warning("%s代碼 %r 不能當檔名,略過該明細檔", what, value)
    return None


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _envelope(result: "CrawlResult") -> dict[str, Any]:
    """每個學期層級檔案共用的頂層欄位。

    **刻意不放 `generated_at`**(schema v2 起)。放了的話每次跑完每個檔的
    內容都會變,發布時等於整包重推 —— 有了 25 年的歷史資料之後,那是每天
    好幾 GB 的無謂流量。時間戳集中在 meta.json 的 `semesters[]`,
    粒度也更正確:一個學期的所有檔案本來就是同一次產生的。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "year": result.year,
        "sem": result.sem,
    }


def _teacher_url(code: str, year: int, sem: int) -> str:
    return f"{BASE_URL}Teach.jsp?format=-3&year={year}&sem={sem}&code={code}"


def _classroom_url(code: str, year: int, sem: int) -> str:
    return f"{BASE_URL}Croom.jsp?format=-3&year={year}&sem={sem}&code={code}"


def _sorted_courses(courses: Iterable[Course]) -> list[dict[str, Any]]:
    return [c.to_dict() for c in sorted(courses, key=lambda c: c.id)]


# --------------------------------------------------------------------------
# 學期層級:系所
# --------------------------------------------------------------------------
def _write_departments(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """學院 / 系所 / 班級三層對照,外加一份學院 → 系所的扁平清單。"""
    course_counts: dict[str, int] = {}
    for course in result.courses:
        for dept_id in course.department_ids:
            course_counts[dept_id] = course_counts.get(dept_id, 0) + 1

    departments = []
    colleges: dict[str | None, list[str]] = {}
    for dept in result.departments:
        payload = dept.to_dict()
        payload["class_groups"] = [
            {"id": g.id, "name": g.name, "url": g.url}
            for g in result.class_groups.get(dept.id, [])
        ]
        payload["course_count"] = course_counts.get(dept.id, 0)
        # 讓使用端不必自己拼路徑
        payload["path"] = _detail_path(result, "courses", dept.id)
        departments.append(payload)
        colleges.setdefault(dept.college, []).append(dept.id)

    payload = _envelope(result)
    payload["departments"] = departments
    # 行政單位(教務處、體育室…)沒有學院,`college` 是 null,獨立成一組
    payload["colleges"] = [
        {"name": name, "department_ids": ids} for name, ids in colleges.items()
    ]
    _write_json(semester_dir / "departments.json", payload, pretty)


def _detail_path(result: "CrawlResult", kind: str, code: str | None) -> str | None:
    safe = _safe_id(code, kind)
    if safe is None:
        return None
    return f"{result.semester}/{kind}/{safe}.json"


def _write_courses(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """每個系所一個檔。合開課程會同時寫進每個開課系所,讓每個檔都是自足的。"""
    by_dept: dict[str, list[Course]] = {d.id: [] for d in result.departments}
    for course in result.courses:
        for dept_id in course.department_ids:
            by_dept.setdefault(dept_id, []).append(course)

    names = {d.id: d for d in result.departments}
    for dept_id, courses in by_dept.items():
        safe = _safe_id(dept_id, "系所")
        if safe is None:
            continue
        dept = names.get(dept_id)
        payload = _envelope(result)
        payload["department"] = dept.to_dict() if dept else {"id": dept_id}
        payload["course_count"] = len(courses)
        payload["courses"] = _sorted_courses(courses)
        _write_json(semester_dir / "courses" / f"{safe}.json", payload, pretty)


# --------------------------------------------------------------------------
# 學期層級:教師
# --------------------------------------------------------------------------
def _write_teachers(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """教師索引 + 每位教師一個課表檔。

    key 一律用**教師代碼**而不是姓名 —— 115-1 實測 803 個代碼只對到 801 個
    姓名,確實有同名老師。用姓名當 key 會把兩個人的課混在一起。
    """
    # code(或無代碼時退回姓名)→ (顯示名稱, 代碼 or None, 課程)
    buckets: dict[str, dict[str, Any]] = {}
    for course in result.courses:
        for index, name in enumerate(course.teachers):
            code = (
                course.teacher_codes[index]
                if index < len(course.teacher_codes)
                else ""
            )
            key = code or f"name:{name}"
            bucket = buckets.setdefault(
                key, {"id": code or None, "name": name, "courses": []}
            )
            bucket["courses"].append(course)

    entries = []
    for bucket in buckets.values():
        courses: list[Course] = bucket["courses"]
        code: str | None = bucket["id"]
        dept_ids = _unique(d for c in courses for d in c.department_ids)
        safe = _safe_id(code, "教師") if code else None

        entries.append(
            {
                "id": code,
                "name": bucket["name"],
                "course_count": len(courses),
                "department_ids": dept_ids,
                # 沒有教師代碼的課(例如純文字欄位)無法產生明細檔,path 為 null
                "path": f"{result.semester}/teachers/{safe}.json" if safe else None,
            }
        )

        if safe is None:
            continue
        payload = _envelope(result)
        payload["teacher"] = {
            "id": code,
            "name": bucket["name"],
            "department_ids": dept_ids,
            "url": _teacher_url(safe, result.year, result.sem),
        }
        payload["course_count"] = len(courses)
        payload["courses"] = _sorted_courses(courses)
        _write_json(semester_dir / "teachers" / f"{safe}.json", payload, pretty)

    entries.sort(key=lambda e: (e["name"], e["id"] or ""))

    payload = _envelope(result)
    payload["teacher_count"] = len(entries)
    payload["teachers"] = entries
    _write_json(semester_dir / "teachers.json", payload, pretty)


# --------------------------------------------------------------------------
# 學期層級:班級
# --------------------------------------------------------------------------
def _write_classes(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """班級索引 + 每個班級一個課表檔(「我要看資工四的課」最常用的入口)。"""
    groups = {
        group.id: (dept, group)
        for dept in result.departments
        for group in result.class_groups.get(dept.id, [])
    }

    by_class: dict[str, list[Course]] = {cid: [] for cid in groups}
    for course in result.courses:
        for class_id in course.class_ids:
            by_class.setdefault(class_id, []).append(course)

    entries = []
    for class_id, courses in by_class.items():
        pair = groups.get(class_id)
        dept, group = pair if pair else (None, None)
        safe = _safe_id(class_id, "班級")

        entries.append(
            {
                "id": class_id,
                "name": group.name if group else None,
                "department_id": dept.id if dept else None,
                "department_name": dept.name if dept else None,
                "college": dept.college if dept else None,
                "course_count": len(courses),
                "url": group.url if group else None,
                "path": f"{result.semester}/classes/{safe}.json" if safe else None,
            }
        )

        if safe is None:
            continue
        payload = _envelope(result)
        payload["class_group"] = {
            "id": class_id,
            "name": group.name if group else None,
            "department_id": dept.id if dept else None,
            "department_name": dept.name if dept else None,
            "college": dept.college if dept else None,
            "url": group.url if group else None,
        }
        payload["course_count"] = len(courses)
        payload["courses"] = _sorted_courses(courses)
        _write_json(semester_dir / "classes" / f"{safe}.json", payload, pretty)

    entries.sort(key=lambda e: e["id"])

    payload = _envelope(result)
    payload["class_count"] = len(entries)
    payload["classes"] = entries
    _write_json(semester_dir / "classes.json", payload, pretty)


# --------------------------------------------------------------------------
# 學期層級:學程 / 教室 / 時段
#
# 這三個維度只有名稱沒有安全的代碼(學程),或數量不值得一個維度開幾百個檔,
# 所以只出清單檔並附課號;要細節就拿課號去 index.json 或系所檔查。
# --------------------------------------------------------------------------
def _write_programs(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """跨領域學程 / 微學程 → 課號。學程只有中文名稱,沒有代碼可當檔名。"""
    buckets: dict[str, list[str]] = {}
    for course in result.courses:
        for program in course.programs:
            buckets.setdefault(program, []).append(course.id)

    programs = [
        {"name": name, "course_count": len(ids), "course_ids": sorted(ids)}
        for name, ids in sorted(buckets.items())
    ]

    payload = _envelope(result)
    payload["program_count"] = len(programs)
    payload["programs"] = programs
    _write_json(semester_dir / "programs.json", payload, pretty)


def _write_classrooms(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """教室 → 課號。可以拿來找空教室,或看某間教室排了什麼課。"""
    buckets: dict[tuple[str | None, str], list[str]] = {}
    for course in result.courses:
        for index, name in enumerate(course.classrooms):
            code = (
                course.classroom_codes[index]
                if index < len(course.classroom_codes)
                else ""
            )
            buckets.setdefault((code or None, name), []).append(course.id)

    classrooms = []
    for (code, name), ids in sorted(buckets.items(), key=lambda kv: kv[0][1]):
        safe = _safe_id(code, "教室") if code else None
        classrooms.append(
            {
                "id": code,
                "name": name,
                "course_count": len(ids),
                "course_ids": sorted(ids),
                "url": _classroom_url(safe, result.year, result.sem) if safe else None,
            }
        )

    payload = _envelope(result)
    payload["classroom_count"] = len(classrooms)
    payload["classrooms"] = classrooms
    _write_json(semester_dir / "classrooms.json", payload, pretty)


def _write_schedule(result: "CrawlResult", semester_dir: Path, pretty: bool) -> None:
    """星期 × 節次 → 課號。排課表、找空堂、檢查衝堂都從這裡查最快。"""
    order = [entry["code"] for entry in period_table()]
    buckets: dict[int, dict[str, list[str]]] = {}
    for course in result.courses:
        for slot in course.time_slots:
            day = buckets.setdefault(slot.day, {})
            for period in slot.periods:
                day.setdefault(period, []).append(course.id)

    days = []
    for day in sorted(buckets):
        periods = buckets[day]
        # 先照 meta.json 的節次順序,沒對照到的未知代碼排在後面(不丟掉)
        codes = [c for c in order if c in periods]
        codes += sorted(c for c in periods if c not in order)
        days.append(
            {
                "day": day,
                "day_name": _day_name(day),
                "periods": [
                    {"code": c, "course_count": len(periods[c]),
                     "course_ids": sorted(periods[c])}
                    for c in codes
                ],
            }
        )

    payload = _envelope(result)
    payload["periods"] = period_table()
    payload["days"] = days
    _write_json(semester_dir / "schedule.json", payload, pretty)


def _day_name(day: int) -> str:
    from .periods import DAY_NAMES

    return DAY_NAMES[day] if 0 <= day < len(DAY_NAMES) else str(day)


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
def _index_entry(course: Course, year: int, sem: int) -> dict[str, Any]:
    """索引只放「篩選 / 搜尋會用到」的欄位,細節留在明細檔。

    `teacher_codes` / `class_ids` 看起來冗,但少了它們就沒辦法從搜尋結果
    直接跳到 `teachers/{code}.json` 或 `classes/{id}.json`,那索引就半殘了。
    """
    return {
        "id": course.id,
        "name_zh": course.name_zh,
        "teachers": list(course.teachers),
        "teacher_codes": list(course.teacher_codes),
        "time_slots": [s.to_dict() for s in course.time_slots],
        "department_ids": list(course.department_ids),
        "class_ids": list(course.class_ids),
        "credits": course.credits,
        "required": course.required,
        "requirement_type": course.requirement_type,
        "year": year,
        "sem": sem,
    }


def _write_semester_index(
    result: "CrawlResult", semester_dir: Path, pretty: bool
) -> None:
    """單一學期的輕量索引。只想查當學期的人不必載跨學期的大檔。"""
    payload = _envelope(result)
    payload["course_count"] = len(result.courses)
    payload["courses"] = [
        _index_entry(c, result.year, result.sem) for c in result.courses
    ]
    _write_json(semester_dir / "index.json", payload, pretty)


def _write_index(result: "CrawlResult", out_dir: Path, pretty: bool) -> None:
    """跨學期的總索引。

    **只涵蓋最新的 INDEX_SEMESTERS 個學期**(schema v2 起)。把 90 學年度
    以來的每個學期都塞進來會膨脹到數十 MB,而且它每次跑都會重寫 ——
    對一個每 4 小時發布一次的靜態站台來說代價太高。歷史學期請改查
    `{semester}/index.json`,那些檔案抓過一次就不會再變。

    `covers` 欄位明講這份索引涵蓋哪些學期,使用端不必自己猜。
    """
    path = out_dir / "index.json"
    existing = _read_json(path) or {}
    kept = [
        entry
        for entry in existing.get("courses", [])
        if (entry.get("year"), entry.get("sem")) != (result.year, result.sem)
    ]
    courses = kept + [_index_entry(c, result.year, result.sem) for c in result.courses]

    # 先算出合併後有哪些學期,取最新的幾個,其餘整批丟掉
    present = {
        (entry["year"], entry["sem"])
        for entry in courses
        if isinstance(entry.get("year"), int) and isinstance(entry.get("sem"), int)
    }
    covered = sorted(present, reverse=True)[:INDEX_SEMESTERS]
    courses = [
        entry for entry in courses if (entry.get("year"), entry.get("sem")) in set(covered)
    ]

    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "covers": [f"{year}-{sem}" for year, sem in covered],
            "course_count": len(courses),
            "courses": courses,
        },
        pretty,
    )


def _write_meta(result: "CrawlResult", out_dir: Path, pretty: bool) -> None:
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
            "partial": result.partial,
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
            "latest": semesters[0]["path"] if semesters else None,
            "semesters": semesters,
            "endpoints": _endpoint_table(),
            "periods": period_table(),
            "requirement_symbols": requirement_table(),
        },
        pretty,
    )


def _endpoint_table() -> list[dict[str, str]]:
    """把有哪些端點寫進 meta.json,使用端不必回頭讀 README 才知道能查什麼。"""
    return [
        {"path": "index.json", "description": "全學期課程輕量索引"},
        {"path": "{semester}/index.json", "description": "單一學期課程輕量索引"},
        {"path": "{semester}/departments.json", "description": "學院 / 系所 / 班級對照"},
        {"path": "{semester}/courses/{department_id}.json", "description": "系所課表"},
        {"path": "{semester}/teachers.json", "description": "教師清單"},
        {"path": "{semester}/teachers/{teacher_id}.json", "description": "教師課表"},
        {"path": "{semester}/classes.json", "description": "班級清單"},
        {"path": "{semester}/classes/{class_id}.json", "description": "班級課表"},
        {"path": "{semester}/programs.json", "description": "學程 → 課號"},
        {"path": "{semester}/classrooms.json", "description": "教室 → 課號"},
        {"path": "{semester}/schedule.json", "description": "星期 × 節次 → 課號"},
        {"path": "errors.json", "description": "最近一次抓取失敗的單位"},
    ]


def _write_errors(result: "CrawlResult", out_dir: Path, pretty: bool) -> None:
    """沒有錯誤時也要寫,不然使用者會看到上一輪殘留的錯誤檔。

    多學期一起跑時,只替換本學年期的錯誤,其他學期的保留。
    """
    path = out_dir / "errors.json"
    existing = _read_json(path) or {}
    # 只保留「明確標了別的學年期」的錯誤。沒標學年期的是舊格式殘留,
    # 留著會變成永遠清不掉的幽靈錯誤,直接丟掉。
    kept = [
        entry
        for entry in existing.get("errors", [])
        if isinstance(entry.get("year"), int)
        and isinstance(entry.get("sem"), int)
        and (entry["year"], entry["sem"]) != (result.year, result.sem)
    ]
    errors = kept + [
        {"year": result.year, "sem": result.sem, **error} for error in result.errors
    ]

    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "year": result.year,
            "sem": result.sem,
            "error_count": len(errors),
            "errors": errors,
        },
        pretty,
    )


def _unique(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def read_semester_times(out_dir: Path) -> dict[tuple[int, int], datetime]:
    """讀既有的 meta.json,回傳每個學年期上次產生的時間(UTC)。

    用來判斷「這學期的資料還新不新」,決定要不要再抓一次。
    檔案不存在、壞掉、或時間格式看不懂時一律當作「沒有資料」,
    最壞的結果只是多抓一次,不會漏抓。
    """
    meta = _read_json(Path(out_dir) / "meta.json") or {}
    times: dict[tuple[int, int], datetime] = {}
    for entry in meta.get("semesters", []):
        year, sem = entry.get("year"), entry.get("sem")
        raw = entry.get("generated_at")
        if not isinstance(year, int) or not isinstance(sem, int) or not raw:
            continue
        # 局部抓取(--dept)的結果不算數,下次還是要完整抓一次
        if entry.get("partial"):
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            log.warning("meta.json 裡 %s-%s 的 generated_at %r 看不懂", year, sem, raw)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        times[(year, sem)] = stamp
    return times
