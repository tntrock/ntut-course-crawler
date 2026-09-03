"""解析課程列表頁(format=-4)。

純函式:吃 HTML 字串 → 吐 dataclass,不發任何網路請求。

版面特性全部來自 tests/fixtures/course_list_real.html 的實測結果,
不是憑印象寫的(plan.md §5.4)。
"""

from __future__ import annotations

import logging
import re

from .models import REQUIREMENT_SYMBOLS, Course, TimeSlot
from .parse_util import absolute_url, clean, query_param, soup_of
from .periods import DAY_COUNT, parse_period_cell

log = logging.getLogger(__name__)

#: 課程表格固定 23 欄,欄位順序即下方索引
COLUMN_COUNT = 23

COL_ID = 0
COL_NAME = 1
COL_STAGE = 2
COL_CREDITS = 3
COL_HOURS = 4
COL_REQUIREMENT = 5
COL_TEACHER = 6
COL_DAY_FIRST = 7  # 日 一 二 三 四 五 六 共 7 欄
COL_CLASSROOM = 14
COL_QUOTA = 15
COL_WITHDRAWN = 16
COL_LANGUAGE = 17
COL_SYLLABUS = 18
COL_NOTES = 19
COL_AUDIT = 20
COL_LAB = 21
COL_PROGRAMS = 22

_COURSE_ID_RE = re.compile(r"^\d+$")


def parse_courses(html: str) -> list[Course]:
    """解析課程列表頁,回傳該班級的所有課程。

    會跳過的列:
    - 開頭的「班週會及導師時間」(課號欄空白,不是真的課)
    - 結尾的「小計」列(課號欄是文字)
    判斷一律看**課號欄是否為純數字**,不用列的位置,學校加減列也不會壞。
    """
    soup = soup_of(html)
    table = _find_course_table(soup)
    if table is None:
        log.warning("課程頁找不到課程表格,回傳空清單")
        return []

    class_name = _class_name(table)
    courses: list[Course] = []

    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue  # 表頭列(<th>)

        course_id = clean(cells[COL_ID].get_text())
        if not course_id or not _COURSE_ID_RE.match(course_id):
            # 班週會 / 小計 這類非課程列,靜靜跳過
            log.debug("跳過非課程列:%r", course_id)
            continue

        if len(cells) != COLUMN_COUNT:
            log.warning(
                "課號 %s 的欄數是 %d(預期 %d),跳過該列",
                course_id,
                len(cells),
                COLUMN_COUNT,
            )
            continue

        courses.append(_course_from_cells(course_id, cells, class_name))

    return courses


def _course_from_cells(course_id: str, cells: list, class_name: str | None) -> Course:
    teachers, teacher_codes = _links(cells[COL_TEACHER], "Teach.jsp")
    classrooms, classroom_codes = _links(cells[COL_CLASSROOM], "Croom.jsp")
    required, requirement_type = _requirement(cells[COL_REQUIREMENT], course_id)

    return Course(
        id=course_id,
        name_zh=clean(cells[COL_NAME].get_text()) or "",
        # 課程列表頁與教學大綱頁都沒有英文課名,目前無來源(plan.md §7-2)
        name_en=None,
        stage=clean(cells[COL_STAGE].get_text()),
        credits=_number(cells[COL_CREDITS], float, course_id, "學分"),
        hours=_number(cells[COL_HOURS], int, course_id, "時數"),
        required=required,
        requirement_type=requirement_type,
        teachers=teachers,
        teacher_codes=teacher_codes,
        classes=[class_name] if class_name else [],
        time_slots=_time_slots(cells),
        classrooms=classrooms,
        classroom_codes=classroom_codes,
        quota=_number(cells[COL_QUOTA], int, course_id, "人數"),
        withdrawn=_number(cells[COL_WITHDRAWN], int, course_id, "撤選人數"),
        language=clean(cells[COL_LANGUAGE].get_text()),
        syllabus_url=_syllabus_url(cells[COL_SYLLABUS]),
        notes=clean(cells[COL_NOTES].get_text()),
        audit=clean(cells[COL_AUDIT].get_text()),
        lab=clean(cells[COL_LAB].get_text()),
        programs=_split_br(cells[COL_PROGRAMS]),
    )


def _find_course_table(soup):
    """課程表格是頁面上第一個含「課號」表頭的表格。

    不能直接拿 soup.find('table') —— 頁尾還有一張節次對照表。
    """
    for table in soup.find_all("table"):
        headers = {clean(th.get_text()) for th in table.find_all("th")}
        if "課號" in headers:
            return table
    return None


def _class_name(table) -> str | None:
    """班級名稱在表格第一列:`<tr><th colspan=23>資工四`。"""
    for row in table.find_all("tr"):
        headers = row.find_all("th", recursive=False)
        if len(headers) == 1:
            return clean(headers[0].get_text())
        if headers:
            break  # 已經走到 23 欄的表頭列,前面沒有班級列
    log.warning("課程頁抓不到班級名稱")
    return None


def _split_br(cell) -> list[str]:
    """多值欄位以 <BR> 分隔,拆成 list。

    不可以用空白切 —— 課程名稱、教室名稱本身就可能含空白。
    """
    parts = (clean(part) for part in cell.get_text("\n").split("\n"))
    return [part for part in parts if part]


def _links(cell, script: str) -> tuple[list[str], list[str]]:
    """取出欄位裡的名稱與其連結帶的 code(教師 / 教室)。

    名稱與 code 一一對應,索引位置相同。沒有連結時退回純文字,code 補空字串。
    """
    names: list[str] = []
    codes: list[str] = []
    for anchor in cell.find_all("a"):
        href = anchor.get("href") or ""
        if script not in href:
            continue
        name = clean(anchor.get_text())
        if not name:
            continue
        names.append(name)
        codes.append(query_param(href, "code") or "")

    if not names:
        # 有些課沒有教師或教室連結(例:體育、班週會),就只留文字
        names = _split_br(cell)
        codes = [""] * len(names)
    return names, codes


def _requirement(cell, course_id: str) -> tuple[bool | None, str | None]:
    """把「修」欄的符號轉成必選修。

    符號包在 `<A href="Cprog.jsp?format=-5">★</A>` 裡,取 anchor 的文字。
    欄位空白 → None(**不要預設成 False**);未知符號 → None + warning。
    """
    symbol = clean(cell.get_text())
    if symbol is None:
        return None, None
    entry = REQUIREMENT_SYMBOLS.get(symbol)
    if entry is None:
        log.warning("課號 %s 出現未知的必選修符號 %r", course_id, symbol)
        return None, None
    return entry


def _time_slots(cells: list) -> list[TimeSlot]:
    """把 7 個星期欄位轉成結構化的 TimeSlot,沒課的天不產生項目。"""
    slots: list[TimeSlot] = []
    for day in range(DAY_COUNT):
        periods = parse_period_cell(cells[COL_DAY_FIRST + day].get_text())
        if periods:
            slots.append(TimeSlot(day=day, periods=periods))
    return slots


def _syllabus_url(cell) -> str | None:
    anchor = cell.find("a")
    if anchor is None or not anchor.get("href"):
        return None
    return absolute_url(anchor["href"])


def _number(cell, kind, course_id: str, label: str):
    """把欄位轉成數字。空欄位 → None;轉不動 → None + warning,不拋例外。"""
    text = clean(cell.get_text())
    if text is None:
        return None
    try:
        return kind(text)
    except ValueError:
        pass
    # 例如時數寫成 "3.0" 而我們要 int
    try:
        value = float(text)
    except ValueError:
        log.warning("課號 %s 的%s欄 %r 不是數字", course_id, label, text)
        return None
    if kind is int and value != int(value):
        log.warning("課號 %s 的%s欄 %r 不是整數,取整數部分", course_id, label, text)
    return kind(value)
