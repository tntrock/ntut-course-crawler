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
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .config import BASE_URL, INDEX_SEMESTERS, SCHEMA_VERSION, TAIPEI
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

#: changes.json 最多保留幾筆異動事件。只有真的有異動才會長,
#: 500 筆大概是好幾個月份 —— 夠人工回溯,又不會讓檔案無限長大。
CHANGE_EVENT_LIMIT = 500

#: 單次抓取超過這麼多筆異動就折成一筆摘要,不逐筆列出。
#: 一口氣幾百筆有兩種可能 —— 學校真的開了一批課(115-1 實際遇過:一次多了
#: 7 個跨校選課班級、265 門課),或是版面改了 / 抓歪了。兩種都不該逐筆灌進
#: 事件流,那會把先前真正的異動整個推出保留範圍。
CHANGE_BULK_THRESHOLD = 200

#: 摘要裡最多列幾個班級的分組統計。系所頂多 60 個所以全列,
#: 班級可能有幾百個,只留量最大的幾個 —— 異常都集中在前面。
CHANGE_GROUP_LIMIT = 20

#: 摘要裡附幾筆完整事件當樣本。
#: 只有 count 的話,人還是得自己去 diff 才知道發生什麼事,
#: 而那正是這個檔要省掉的工。
CHANGE_SAMPLE_LIMIT = 10

#: 拿來判斷「這門課有沒有變」的欄位。取自索引項目 —— 索引本來就是
#: 「篩選 / 搜尋會用到」的那些欄位,剛好也就是異動了會影響到人的那些。
#: 刻意不比 `teacher_codes`(跟 `teachers` 連動)。
#:
#: **也刻意不比 `enrolled` / `withdrawn`。** 加退選期間每四小時就有上千門課
#: 的人數在動,放進來會讓事件流每次都爆掉 bulk_change,把真正的結構性異動
#: (加開、停開、調課、換老師)整個淹掉。人數的時間軸另外走 enrollment.json。
_TRACKED_FIELDS = (
    "name_zh",
    "teachers",
    "time_slots",
    "credits",
    "required",
    "requirement_type",
    "department_ids",
    "class_ids",
    # 改成全英語授課(或改回中文)是會影響選課決定的異動,而且很低頻 ——
    # 不像人數那樣每四小時就動,放進事件流不會洗版。
    "language",
)

#: 根目錄 syllabus.json 裡每個學期最多記幾門課的抓取狀態。
#: 一個學期兩三千門,留 5000 是為了不必為了「這學期特別多」而改碼。
SYLLABUS_STATE_LIMIT = 5000

#: 根目錄 enrollment.json 保留幾筆快照索引。一個學期約 120 天,
#: 400 筆容得下三個學期的重疊期。
ENROLLMENT_SNAPSHOT_LIMIT = 400

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
    _write_enrollment_snapshot(result, semester_dir, out_dir, pretty)

    # 跨學期
    # 變更紀錄要先算 —— 它的比對基準就是還沒被蓋掉的舊 index.json
    _write_changes(result, out_dir, pretty)
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
    """寫一個 JSON 檔。**先寫暫存檔再 rename**,不直接覆蓋。

    rename 在同一個檔案系統上是原子的,所以任何時刻這個路徑上要嘛是完整的
    舊版、要嘛是完整的新版,不會有寫到一半的殘骸。

    這不是潔癖:Actions 的 job 逾時是直接把行程砍掉的,而 index.json 有
    1.9 MB,寫的過程中被砍掉的機率不算低。發布步驟設了 `always()`(抓到
    一半也要把已經完成的部分推上去),沒有這個保證的話,被截斷的
    index.json 就會直接覆蓋線上那份 —— 那是整個 API 掛掉,比少更新一輪
    嚴重得多。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
# 教學大綱
# --------------------------------------------------------------------------
def write_syllabus(
    out_dir: Path,
    semester: str,
    course_id: str,
    payload: dict[str, Any],
    *,
    pretty: bool = False,
) -> str:
    """寫一門課的教學大綱,回傳它的相對路徑。

    一課一檔而不是整包一個檔:一個學期兩千多門課,每門的大綱動輒好幾 KB,
    合起來是十幾 MB —— 想看一門課的大綱不該先下載整個學期。
    """
    safe = _safe_id(course_id, "課程")
    if safe is None:
        raise ValueError(f"課號 {course_id!r} 不能當檔名")
    path = f"{semester}/syllabus/{safe}.json"
    _write_json(Path(out_dir) / path, payload, pretty)
    return path


def is_frozen_semester(year: int, sem: int, out_dir: Path) -> bool:
    """這個學年期的資料還會不會變?

    只有**最新的那個學期**會變:開學前老師還在改大綱、加退選期間人數天天動。
    學期跑完就定案了,再抓也只是拿回同樣的東西。

    基準是 meta.json 裡已知的學年期,加上當下這一個(第一次抓它的時候
    meta 裡還沒有)。meta.json 讀不到就當作不凍結,最壞的結果是多抓一輪,
    不會漏抓。

    兩個地方用到:回補歷史學期時不重抓已有的大綱,以及不為已經結束的學期
    產生「今天的」人數快照。
    """
    known = set(read_semester_times(out_dir))
    known.add((year, sem))
    return (year, sem) < max(known)


def read_syllabus_state(out_dir: Path) -> dict[str, dict[str, dict[str, str]]]:
    """讀根目錄 `syllabus.json`,回傳 學期 → {課號: {"at": 抓取時間, "hash": 內容雜湊}}。

    用來決定「這門課的大綱抓過了沒、多久以前抓的、內容跟上次一不一樣」。
    放根目錄才能跟 meta / index / errors / changes / enrollment 一起被 workflow
    還原 —— 學期子目錄不在 sparse checkout 的範圍內,放那裡等於每次都失憶。

    schema v2 的值是單純的時間字串,這裡一律正規化成 dict。沒有 `hash` 的
    舊紀錄下次抓到時一定會判定成「內容有變」而重寫一次檔案,那是預期中的
    一次性搬遷,之後就穩定了。
    """
    data = _read_json(Path(out_dir) / "syllabus.json") or {}
    state = data.get("fetched")
    if not isinstance(state, dict):
        return {}

    normalised: dict[str, dict[str, dict[str, str]]] = {}
    for sem, courses in state.items():
        if not isinstance(courses, dict):
            continue
        rows: dict[str, dict[str, str]] = {}
        for cid, entry in courses.items():
            if isinstance(entry, str):  # v2:只有時間戳
                rows[cid] = {"at": entry}
            elif isinstance(entry, dict) and isinstance(entry.get("at"), str):
                rows[cid] = {
                    k: v for k, v in entry.items() if k in ("at", "hash")
                }
        if rows:
            normalised[sem] = rows
    return normalised


def read_syllabus_frozen(out_dir: Path) -> dict[str, dict[str, Any]]:
    """讀根目錄 `syllabus.json` 的 `frozen`,回傳 學期 → 收合時的統計。

    **已收合的學期不再保留逐課狀態。** 理由是大小:一門課的狀態(課號 +
    時間戳 + 雜湊)約 66 bytes,補完 110-1 起的 11 個學期就是兩萬多筆、
    1.6 MB —— 而這個檔每次抓大綱都會整個重寫,等於把剛省下來的 blob
    又用另一種形式吐回去。過去的學期不會再變動,收合成一個門數就夠了。

    收合後 `crawl_syllabi` 會整個跳過那個學期,連 targets 都不算。
    真要重抓,手動把該學期從 `frozen` 裡刪掉即可。
    """
    data = _read_json(Path(out_dir) / "syllabus.json") or {}
    frozen = data.get("frozen")
    if not isinstance(frozen, dict):
        return {}
    return {
        sem: entry for sem, entry in frozen.items() if isinstance(entry, dict)
    }


def _semester_key(semester: str) -> tuple[int, int]:
    """把 "115-1" 排成 (115, 1)。純字串排序會把 99-2 排在 110-1 前面。"""
    try:
        year, _, sem = semester.partition("-")
        return int(year), int(sem)
    except ValueError:
        return (-1, -1)


def syllabus_done_semesters(out_dir: Path) -> set[str]:
    """哪些學期的大綱已經補完了(不必再排進回補批次)。

    兩種算完成:狀態已收合成 `frozen` 的,或進度顯示抓到的門數已經追上
    有大綱連結的門數。後者涵蓋的是**當期**—— 它抓完了但不會收合,因為
    還要靠逐課狀態決定下一輪要重抓哪些。
    """
    data = _read_json(Path(out_dir) / "syllabus.json") or {}
    done = set(read_syllabus_frozen(out_dir))
    for entry in data.get("semesters") or []:
        if not isinstance(entry, dict):
            continue
        semester, fetched, with_url = (
            entry.get("semester"),
            entry.get("fetched"),
            entry.get("with_url"),
        )
        if (
            isinstance(semester, str)
            and isinstance(fetched, int)
            and isinstance(with_url, int)
            and with_url > 0
            and fetched >= with_url
        ):
            done.add(semester)
    return done


def write_syllabus_index(
    out_dir: Path,
    state: dict[str, dict[str, dict[str, str]]],
    totals: dict[str, dict[str, Any]],
    *,
    frozen: dict[str, dict[str, Any]] | None = None,
    pretty: bool = False,
) -> None:
    """根目錄 `syllabus.json`:每個學期抓到哪了,以及逐課的抓取狀態。

    三個區塊各有各的讀者:

    - `semesters` 是給人看的進度(抓了幾門 / 共幾門 / 最舊那筆多久以前)。
    - `fetched` 是給下一次執行看的逐課狀態(抓取時間 + 內容雜湊)。
    - `frozen` 是已經補完、狀態收合掉的歷史學期,只留門數。

    分開放是因為前者小、後者大,前端要顯示進度不必吞下幾千筆時間戳。

    `totals`(共幾門 / 幾門有大綱)只會帶**這次抓過的學期** —— 呼叫端一次
    只處理一個學期,不會知道別的學期有幾門課。所以其他學期的 totals 要從
    舊檔沿用,否則「抓了 1909 門 / 共幾門」的分母會在下一個學期跑完之後
    憑空消失,進度就只剩一個沒有基準的數字。

    `frozen` 同理只帶**這次新收合的**學期,舊檔裡已經收合的要沿用 ——
    它們的逐課狀態已經被丟掉了,舊紀錄是唯一的依據,洗掉等於下次執行
    會把那個學期整個重抓一遍。收合是單向的,所以合併不會有衝突。
    """
    previous = {
        entry["semester"]: entry
        for entry in ((_read_json(Path(out_dir) / "syllabus.json") or {}).get(
            "semesters"
        ) or [])
        if isinstance(entry, dict) and entry.get("semester")
    }
    previous_totals = {
        sem: {key: entry[key] for key in ("course_count", "with_url") if key in entry}
        for sem, entry in previous.items()
    }

    all_frozen = dict(read_syllabus_frozen(out_dir))
    all_frozen.update(frozen or {})

    trimmed = {
        sem: dict(sorted(courses.items())[:SYLLABUS_STATE_LIMIT])
        for sem, courses in state.items()
        if courses and sem not in all_frozen
    }

    entries: dict[str, dict[str, Any]] = {}
    for sem, courses in trimmed.items():
        # v2 的值是裸的時間字串。狀態一般都經過 read_syllabus_state 正規化,
        # 但這裡不假設呼叫端一定走過那條路 —— 進度顯示不值得為此炸掉整批。
        stamps = sorted(
            row["at"] if isinstance(row, dict) else row for row in courses.values()
        )
        entries[sem] = {
            "semester": sem,
            "fetched": len(courses),
            "oldest_fetch": stamps[0] if stamps else None,
            "newest_fetch": stamps[-1] if stamps else None,
        }
    for sem, info in all_frozen.items():
        entries[sem] = {
            "semester": sem,
            "fetched": info.get("fetched"),
            "frozen": True,
            "frozen_at": info.get("at"),
        }

    semesters = []
    for sem in sorted(entries, key=_semester_key, reverse=True):
        entry = entries[sem]
        entry.update(previous_totals.get(sem) or {})
        entry.update(totals.get(sem) or {})
        semesters.append(entry)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "semesters": semesters,
        "fetched": {
            sem: trimmed[sem] for sem in sorted(trimmed, key=_semester_key, reverse=True)
        },
    }
    if all_frozen:
        payload["frozen"] = {
            sem: all_frozen[sem]
            for sem in sorted(all_frozen, key=_semester_key, reverse=True)
        }
    _write_json(Path(out_dir) / "syllabus.json", payload, pretty)


# --------------------------------------------------------------------------
# 修課 / 撤選人數的時間軸
# --------------------------------------------------------------------------
def _write_enrollment_snapshot(
    result: "CrawlResult", semester_dir: Path, out_dir: Path, pretty: bool
) -> None:
    """每天存一份修課 / 撤選人數的快照,並更新根目錄的索引。

    明細檔裡的人數是**當下**的值,每次抓取直接覆蓋 —— 學期結束後那是定案的
    數字,拿來算退選率沒問題;但「哪門課在第幾週被大量退掉」這種問題,
    快照沒留下來就永遠答不了,而且錯過了就要再等一個學期。

    **刻意不走 `changes.json`。** 加退選期間每 4 小時就有上千門課的人數在動,
    塞進事件流會每次都觸發 bulk_change,把真正的結構性異動(加開、停開、
    調課、換老師)整個淹掉。兩種資料的變動頻率差一個數量級,不該共用一個檔。

    一天一檔(依台灣時區切),同一天內重複抓取就覆蓋當天那份 —— 於是每天
    留下的是當天最後一次的狀態。檔案只新增不改寫,對 gh-pages 的歷史很友善。
    """
    if result.partial:
        log.info("局部抓取(--dept),略過人數快照")
        return

    if result.backfill:
        # 回補歷史學期時會走到這裡。那個學期早就結束了,人數是定案的數字,
        # 存成一份「今天的」快照只會在時間軸上多一個假的轉折點。
        log.info("%s 是回補,略過人數快照", result.semester)
        return

    rows = [
        {"id": c.id, "enrolled": c.enrolled, "withdrawn": c.withdrawn}
        for c in result.courses
        if c.enrolled is not None or c.withdrawn is not None
    ]
    if not rows:
        # 太舊的學期整欄都是空的,寫一份全 null 的快照沒有意義
        log.info("%s 沒有任何人數資料,略過人數快照", result.semester)
        return

    now = datetime.now(timezone.utc)
    date = now.astimezone(TAIPEI).strftime("%Y-%m-%d")
    enrolled_total = sum(r["enrolled"] or 0 for r in rows)
    withdrawn_total = sum(r["withdrawn"] or 0 for r in rows)

    payload = _envelope(result)
    payload["date"] = date
    payload["at"] = _now()
    payload["course_count"] = len(rows)
    payload["enrolled_total"] = enrolled_total
    payload["withdrawn_total"] = withdrawn_total
    payload["courses"] = sorted(rows, key=lambda r: r["id"])
    _write_json(semester_dir / "enrollment" / f"{date}.json", payload, pretty)

    log.info(
        "%s 人數快照 %s:%d 門課,修課 %d 人次 / 撤選 %d 人次",
        result.semester,
        date,
        len(rows),
        enrolled_total,
        withdrawn_total,
    )

    _update_enrollment_index(
        out_dir,
        {
            "semester": result.semester,
            "year": result.year,
            "sem": result.sem,
            "date": date,
            "at": payload["at"],
            "course_count": len(rows),
            "enrolled_total": enrolled_total,
            "withdrawn_total": withdrawn_total,
            "path": f"{result.semester}/enrollment/{date}.json",
        },
        pretty,
    )


def _update_enrollment_index(
    out_dir: Path, entry: dict[str, Any], pretty: bool
) -> None:
    """根目錄的 `enrollment.json`:有哪幾天的快照,以及當天的總計。

    放根目錄而不是學期目錄下,是為了能跟 meta / index / errors / changes 一起
    被 workflow 還原 —— 學期目錄的檔案不在 sparse checkout 的範圍內,
    每次抓取都會看不到既有紀錄,索引就永遠只剩今天那一筆。

    只放總計不放逐課明細:光是「全校退選率隨時間的走勢」就靠這一個檔,
    要看是哪幾門課再去拿當天的快照。
    """
    path = Path(out_dir) / "enrollment.json"
    existing = _read_json(path) or {}
    kept = [
        s
        for s in existing.get("snapshots", [])
        if isinstance(s, dict)
        and (s.get("semester"), s.get("date")) != (entry["semester"], entry["date"])
    ]
    snapshots = [entry] + kept
    # 新的在前:先比日期再比學期,同一天有多個學期時新的學期排前面
    snapshots.sort(
        key=lambda s: (str(s.get("date")), s.get("year") or 0, s.get("sem") or 0),
        reverse=True,
    )
    snapshots = snapshots[:ENROLLMENT_SNAPSHOT_LIMIT]

    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        },
        pretty,
    )


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
        # 授課語言。115-1 全校 2,717 門裡有 499 門非中文(英語 488、中英雙語 11),
        # 而「只看全英語授課」是很常見的篩選 —— 沒有它就得下載 60 個系所檔才篩得出來,
        # 也沒有別的端點提供這個維度(不像學程有 programs.json、教室有 classrooms.json)。
        # 代價是索引大 5.2%,值得。中文授課的值是 null。
        "language": course.language,
        # 修課 / 撤選人數。放進索引是為了讓「算全校退選率」不必先下載 60 個
        # 系所明細檔;兩個小整數對索引大小的影響可以忽略。
        "enrolled": course.enrolled,
        "withdrawn": course.withdrawn,
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


# --------------------------------------------------------------------------
# 變更紀錄
# --------------------------------------------------------------------------
def _write_changes(result: "CrawlResult", out_dir: Path, pretty: bool) -> None:
    """把這一輪偵測到的異動,逐筆追加進 `changes.json` 的事件流。

    每 4 小時重寫一整包 JSON,光看檔案時間戳分不出「只是重跑」和「學校真的
    動了課」。這個檔就是把「最近發生了什麼」寫下來:**一筆異動一個事件**,
    最新的在最前面,直接讀開頭幾筆就是最近的異動。

    比對基準是**上一輪的頂層 `index.json`**(所以必須在 `_write_index`
    覆蓋它之前呼叫)。基準只涵蓋最新的 `INDEX_SEMESTERS` 個學期,回補歷史
    學期時沒有東西可比,會記一筆 baseline 而不是「新增兩千多門課」。
    """
    if result.partial:
        # --dept 只抓了幾個系所,拿它跟全校的索引比會得到「移除兩千門課」
        log.info("局部抓取(--dept),略過變更紀錄")
        return

    baseline = _previous_index_entries(out_dir, result.year, result.sem)
    current = {c.id: _index_entry(c, result.year, result.sem) for c in result.courses}
    stamp = _now()

    if baseline is None:
        # 第一次抓這個學期,或它已經被擠出頂層索引的涵蓋範圍
        log.info("%s 沒有可比對的基準,記一筆 baseline", result.semester)
        events = [
            {
                "at": stamp,
                "semester": result.semester,
                "type": "baseline",
                "course_count": len(current),
            }
        ]
    else:
        events = _course_events(baseline, current, result.semester, stamp)
        events += _teacher_events(baseline, current, result.semester, stamp)
        events = _collapse_if_bulk(events, result.semester, stamp)

    _append_events(out_dir, events, stamp, pretty)


def _course_events(
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    semester: str,
    stamp: str,
) -> list[dict[str, Any]]:
    """加開 / 停開 / 內容變更,一門課一筆。"""
    events: list[dict[str, Any]] = []

    for cid in sorted(set(current) - set(baseline)):
        events.append(_course_event(stamp, semester, "course_added", current[cid]))
    for cid in sorted(set(baseline) - set(current)):
        events.append(_course_event(stamp, semester, "course_removed", baseline[cid]))
    for cid in sorted(set(current) & set(baseline)):
        changed = _diff_fields(baseline[cid], current[cid])
        if changed:
            events.append(
                {
                    **_course_event(stamp, semester, "course_changed", current[cid]),
                    "changes": changed,
                }
            )

    return events


def _course_event(
    stamp: str, semester: str, kind: str, entry: dict[str, Any]
) -> dict[str, Any]:
    """一筆課程事件。只留認得出「是哪一門課」的欄位,細節去索引查。"""
    return {
        "at": stamp,
        "semester": semester,
        "type": kind,
        "id": entry.get("id"),
        "name": entry.get("name_zh"),
        "teachers": entry.get("teachers", []),
        "department_ids": entry.get("department_ids", []),
        "class_ids": entry.get("class_ids", []),
    }


def _teacher_events(
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    semester: str,
    stamp: str,
) -> list[dict[str, Any]]:
    """這學期多了誰、少了誰。

    「少了一位老師」不等於「有課停開」—— 換人授課時課還在,只有教師端看得
    出來;反過來合開課多掛一位老師也一樣。兩個維度都記才看得到全貌。
    """
    before = _teacher_index(baseline)
    after = _teacher_index(current)
    events: list[dict[str, Any]] = []

    for key in sorted(set(after) - set(before)):
        events.append(_teacher_event(stamp, semester, "teacher_added", after[key]))
    for key in sorted(set(before) - set(after)):
        events.append(_teacher_event(stamp, semester, "teacher_removed", before[key]))

    return events


def _teacher_index(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """索引項目 → 每位教師開了哪些課。

    key 用**教師代碼**,和 `_write_teachers` 同一套規則 —— 115-1 實測 803 個
    代碼只對到 801 個姓名,用姓名當 key 會把兩個同名老師併成一個人,
    於是其中一位停開就會誤報成「這位老師消失了」。
    """
    teachers: dict[str, dict[str, Any]] = {}
    for entry in entries.values():
        names = entry.get("teachers") or []
        codes = entry.get("teacher_codes") or []
        for index, name in enumerate(names):
            code = codes[index] if index < len(codes) else ""
            key = code or f"name:{name}"
            bucket = teachers.setdefault(
                key,
                {
                    "id": code or None,
                    "name": name,
                    "course_count": 0,
                    "department_ids": [],
                },
            )
            bucket["course_count"] += 1
            for dept in entry.get("department_ids") or []:
                if dept not in bucket["department_ids"]:
                    bucket["department_ids"].append(dept)
    return teachers


def _teacher_event(
    stamp: str, semester: str, kind: str, bucket: dict[str, Any]
) -> dict[str, Any]:
    return {
        "at": stamp,
        "semester": semester,
        "type": kind,
        "id": bucket["id"],
        "name": bucket["name"],
        "course_count": bucket["course_count"],
        "department_ids": bucket["department_ids"],
    }


def _collapse_if_bulk(
    events: list[dict[str, Any]], semester: str, stamp: str
) -> list[dict[str, Any]]:
    """一次幾百筆異動的話,折成一筆**帶分組統計**的摘要。

    逐筆灌進去會把先前真正的異動整個推出保留範圍,所以要折;但只留一個
    總數又等於什麼都沒說 —— 人還是得自己去 diff 才知道發生什麼事,而那
    正是這個檔要省掉的工。

    所以摘要要能直接回答「這批是什麼」:依系所與班級分組的數量、加上幾筆
    完整樣本。115-1 實際遇過的那次(265 門新增)長這樣 ——

        by_department: {"01": 185, "14": 80}
        by_class:      {"589": 80, "2519": 42, "2520": 38, ...}

    兩個單位、7 個班級,一眼就看得出是「學校開了一批跨校選課」而不是
    「解析器壞了」。真的抓歪時分組會散落在幾十個系所,一樣一眼分得出來。
    """
    if len(events) <= CHANGE_BULK_THRESHOLD:
        return events

    counts = _tally(events, lambda e: [e["type"]])
    by_department = _tally(events, lambda e: e.get("department_ids") or [])
    by_class = _tally(events, lambda e: e.get("class_ids") or [])

    log.warning(
        "%s 一次偵測到 %d 筆異動(%s),超過 %d 筆上限,折成摘要。"
        "系所分布 %s;班級分布 %s。集中在少數幾組通常是學校開了一批課,"
        "散落在幾十個系所則多半是版面改了或抓歪了 —— 兩種都建議人工確認。",
        semester,
        len(events),
        ", ".join(f"{k} {v}" for k, v in counts.items()),
        CHANGE_BULK_THRESHOLD,
        by_department or "(無)",
        _head(by_class, CHANGE_GROUP_LIMIT) or "(無)",
    )
    return [
        {
            "at": stamp,
            "semester": semester,
            "type": "bulk_change",
            "event_count": len(events),
            "counts": counts,
            # 系所頂多 60 個所以全列;班級可能有幾百個,只留量最大的幾個
            "by_department": by_department,
            "by_class": _head(by_class, CHANGE_GROUP_LIMIT),
            "samples": events[:CHANGE_SAMPLE_LIMIT],
            "note": "異動量超過上限,未逐筆列出;分組統計與樣本見上",
        }
    ]


def _tally(events: list[dict[str, Any]], keys) -> dict[str, int]:
    """依 `keys(event)` 取出的每個鍵計數,由多到少排序。

    一門課可能同時掛在多個系所 / 班級(合開、跨校),所以是一對多 ——
    分組數量的總和會大於事件數,這是對的。
    """
    counts: dict[str, int] = {}
    for event in events:
        for key in keys(event):
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _head(counts: dict[str, int], limit: int) -> dict[str, int]:
    return dict(list(counts.items())[:limit])


def _previous_index_entries(
    out_dir: Path, year: int, sem: int
) -> dict[str, dict[str, Any]] | None:
    """上一輪頂層索引裡屬於這個學年期的課程,課號 → 索引項目。

    完全沒有這個學年期的資料時回 `None`(而不是空 dict)—— 「上一輪有零門課」
    和「上一輪根本沒抓過」對變更紀錄來說是完全不同的兩件事。
    """
    index = _read_json(Path(out_dir) / "index.json")
    if not index:
        return None
    entries = {
        entry["id"]: entry
        for entry in index.get("courses", [])
        if isinstance(entry, dict)
        and entry.get("id")
        and (entry.get("year"), entry.get("sem")) == (year, sem)
    }
    return entries or None


def _diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """回傳有變動的欄位與前後值。沒變就是空 dict。

    **只比對兩邊都有的欄位。** 比對基準是上一輪發布的索引,而索引的欄位會
    隨程式演進而增加 —— 新欄位第一次出現時,舊索引裡根本沒有那個 key,
    用 `.get()` 會讀成 None,於是「從 None 變成 英語」就被當成學校改了課。

    實際會發生什麼:2026-09-05 把 `language` 加進索引時,全校 499 門非中文
    課會一次全部變成 course_changed,直接觸發一筆假的 bulk_change,把真正的
    異動洗掉。那不是學校動了資料,是我們加了欄位。

    欄位消失時同理(改了程式而不是學校改了課),一樣不報。
    """
    return {
        field: {"from": before[field], "to": after[field]}
        for field in _TRACKED_FIELDS
        if field in before and field in after and before[field] != after[field]
    }


def _append_events(
    out_dir: Path, events: list[dict[str, Any]], stamp: str, pretty: bool
) -> None:
    """把這一輪的事件插到最前面,並砍掉超過上限的舊事件。

    `checked_at` 每次都更新,`events` 只有真的有異動才長 —— 這樣「學校沒動」
    和「爬蟲壞了好幾天沒跑」才分得出來。
    """
    path = Path(out_dir) / "changes.json"
    existing = _read_json(path) or {}
    kept = [e for e in existing.get("events", []) if isinstance(e, dict)]

    if events:
        summary = ", ".join(
            f"{e['type']} {e.get('name') or e.get('id') or ''}".strip()
            for e in events[:10]
        )
        log.info(
            "偵測到 %d 筆異動:%s%s",
            len(events),
            summary,
            " …" if len(events) > 10 else "",
        )
    else:
        log.info("與上一輪比對後沒有任何異動")

    merged = (events + kept)[:CHANGE_EVENT_LIMIT]
    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": stamp,
            # 最後一次「比對過」的時間。沒有異動時 events 不會長,靠這個欄位
            # 才分得出「學校沒動」與「爬蟲根本沒跑」。
            "checked_at": stamp,
            "event_count": len(merged),
            "events": merged,
        },
        pretty,
    )


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
        {"path": "errors.json", "description": "各學期抓取失敗的單位"},
        {"path": "runs.json", "description": "最近的抓取執行紀錄(含失敗與逾時)"},
        {"path": "changes.json", "description": "最近的課程與教師異動事件"},
        {"path": "enrollment.json", "description": "修課 / 撤選人數快照的索引"},
        {"path": "syllabus.json", "description": "教學大綱的抓取進度"},
        {
            "path": "{semester}/syllabus/{course_id}.json",
            "description": "單一課程的教學大綱與進度",
        },
        {
            "path": "{semester}/enrollment/{date}.json",
            "description": "某一天的逐課修課 / 撤選人數",
        },
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


def write_semester_failure(
    out_dir: Path,
    year: int,
    sem: int,
    error: BaseException,
    *,
    pretty: bool = False,
) -> None:
    """整個學期抓不動時,把失敗記進 errors.json。

    **刻意不碰 meta.json** —— 在那裡留下紀錄會讓下次執行以為這學期抓過了,
    於是永遠不會重試。errors.json 是「這次發生什麼事」,meta.json 是
    「我手上有什麼資料」,兩者不能混。

    下次同一個學期抓成功時,`_write_errors` 會依 (year, sem) 換掉這筆。
    """
    path = Path(out_dir) / "errors.json"
    existing = _read_json(path) or {}
    kept = [
        entry
        for entry in existing.get("errors", [])
        if isinstance(entry.get("year"), int)
        and isinstance(entry.get("sem"), int)
        and (entry["year"], entry["sem"]) != (year, sem)
    ]
    kept.append(
        {
            "year": year,
            "sem": sem,
            "stage": "semester",
            "error": f"{type(error).__name__}: {error}",
        }
    )
    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "year": year,
            "sem": sem,
            "error_count": len(kept),
            "errors": kept,
        },
        pretty,
    )


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
