"""解析教學大綱頁(`ShowSyllabus.jsp`)。

版面是一張 `<tr><th>標籤<td>內容</tr>` 的兩欄表格,長文欄位包在 `<textarea>`
裡(所以 `get_text()` 拿得到),清單欄位用 `<br>` 分行、每行以 `●` 開頭。

**未知標籤不丟掉。** 學校加了新欄位時(SDGs 與「是否導入 AI」顯然就是近年
才加的),認不得的標籤會原樣收進 `extra`,而不是靜靜消失 —— 我們寧可輸出
多一個沒人用的欄位,也不要哪天才發現漏抓了半年。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .config import TAIPEI
from .parse_util import clean, soup_of

log = logging.getLogger("crawler.parse_syllabus")

#: 課程基本資料表格裡課號那一欄的位置(學年期 課號 課程名稱 …)。
_BASIC_COL_ID = 1

#: 課不在開課資料裡時,頁面頂端的紅字。實測樣本:
#: `錯誤訊息 : 查無課號 (364585) 的開課資料`
_MISSING_COURSE_RE = re.compile(r"查無課號\s*\((\d+)\)")

#: 標籤 → 輸出欄位名。key 是拿掉空白後的標籤文字。
_FIELDS = {
    "教師姓名": "teacher_name",
    "Email": "teacher_email",
    "最後更新時間": "updated_at",
    "課程大綱": "outline",
    "課程進度(1-16週)": "schedule",
    "彈性學習(17-18週)": "flexible_learning",
    "評量方式與標準": "assessment",
    "使用教材、參考書目或其他": "materials",
    "課程諮詢管道": "contact",
    "延伸教學與資源": "extended_resources",
    "課程對應SDGs指標": "sdgs",
    "課程是否導入AI": "ai_usage",
    "備註": "notes",
}

#: 以 `<br>` 分行、逐項輸出成陣列的欄位。其餘一律保留原樣的長文字 ——
#: 課程進度那種逐週的內容,自作聰明切開只會切壞。
_LIST_FIELDS = {"sdgs", "ai_usage", "extended_resources", "contact"}

#: 彈性學習那張巢狀表格的標籤 → 欄位名。
_FLEX_FIELDS = {
    "類別": "category",
    "內容": "content",
    "時數(小時)": "hours",
    "學習成果": "outcome",
    "評量比例": "assessment_ratio",
}

#: 清單項目開頭的項目符號。
_BULLET_RE = re.compile(r"^[●○•●○]\s*")


def course_exists(html: str, course_id: str) -> bool | None:
    """學校的開課資料裡還有這門課嗎?`True` 有、`False` 沒有、`None` 判不出來。

    給停開判定用。異動偵測原本是**用缺席推論**停開的 —— 上一輪有、這一輪
    沒有就記一筆,而「課停開了」跟「我們這一輪讀錯了」在輸出上一模一樣。
    這一頁提供正面證據:課不在了,學校會明講「查無課號 (N) 的開課資料」。

    判準是**課程基本資料那一列**,不是有沒有大綱內容(2026-09-09 實抓確認):

    - `code` 參數不影響這一列。`snum=364585` 配對的 code 與別門課的 code
      回傳的頁面逐字相同 —— 所以查存在只需要課號,不必存 `syllabus_url`。
    - 但 `code` 影響**大綱內容**:不帶 code 時課程基本資料照樣完整渲染,
      下面的大綱區塊卻是「尚未登錄」,跟停開的頁面長得一樣。拿大綱內容
      當判準會把還在的課判成停開。

    對不上課號一律回 `None`,不回 `False` —— 拿到別門課的頁面、或版面改了,
    都是「判不出來」。**判不出來絕不可以當成停開**:這個函式存在的理由就是
    要有證據才記停開,沒證據時退回猜測等於什麼都沒做。
    """
    soup = soup_of(html)
    table = _find_basic_table(soup)
    if table is None:
        log.warning("大綱頁找不到課程基本資料表格,無法判定課號 %s", course_id)
        return None

    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) >= _BASIC_COL_ID + 1:
            if clean(cells[_BASIC_COL_ID].get_text()) == course_id:
                return True

    # 沒有那一列 → 看有沒有明確的錯誤訊息。「查無課號 (N)」的 N 要對得上,
    # 不然拿到的是別門課的頁面,那是判不出來,不是停開。
    missing = _MISSING_COURSE_RE.search(soup.get_text(" "))
    if missing:
        return False if missing.group(1) == course_id else None

    log.warning("大綱頁既沒有課號 %s 那一列,也沒有錯誤訊息,判不出來", course_id)
    return None


def _find_basic_table(soup):
    """課程基本資料表格:第一個表頭同時有「學年期」與「課號」的表格。

    不能拿第一個 `<table>` —— 下面還有大綱內容那張,以及彈性學習的巢狀表格。
    """
    for table in soup.find_all("table"):
        headers = {clean(th.get_text()) for th in table.find_all("th")}
        if "學年期" in headers and "課號" in headers:
            return table
    return None


def parse_syllabus(html: str) -> dict[str, Any]:
    """把一頁教學大綱解析成 dict。

    抓不到內容表格時回空 dict —— 有些課根本沒填大綱,那不是錯誤,
    呼叫端據此決定要不要輸出檔案。
    """
    soup = BeautifulSoup(html, "lxml")
    table = _find_syllabus_table(soup)
    if table is None:
        return {}

    out: dict[str, Any] = {}
    extra: dict[str, str] = {}

    for row in table.find_all("tr", recursive=False):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 2:
            continue
        label = clean(cells[0].get_text()) or ""
        key = _FIELDS.get(re.sub(r"\s+", "", label))
        value_cell = cells[1]

        if key is None:
            if label:
                text = _text(value_cell)
                if text:
                    extra[label] = text
            continue

        if key == "flexible_learning":
            out[key] = _flexible_learning(value_cell)
        elif key == "teacher_name":
            out[key] = _teacher_name(value_cell)
        elif key == "teacher_email":
            out[key] = _email(value_cell)
        elif key == "updated_at":
            out[key] = _timestamp(_text(value_cell))
        elif key in _LIST_FIELDS:
            out[key] = _lines(value_cell)
        else:
            out[key] = _text(value_cell)

    if extra:
        log.info("教學大綱有沒認過的欄位,已收進 extra:%s", ", ".join(extra))
        out["extra"] = extra

    return out


def _find_syllabus_table(soup):
    """內容表格是第一個含「最後更新時間」的表格。

    不能只取「第二張表」—— 頁面最上面那張是課程摘要,而課程摘要在沒有
    大綱的課上可能不存在,位置靠不住。
    """
    for table in soup.find_all("table"):
        if "最後更新時間" in table.get_text():
            # 巢狀表格(彈性學習)也會match,取最外層的那張
            if table.find_parent("table") is None:
                return table
    return None


def _text(cell) -> str | None:
    """把一格的文字正規化。`<br>` 換成換行,前後空白去掉。"""
    for br in cell.find_all("br"):
        br.replace_with("\n")
    raw = cell.get_text()
    lines = [line.strip() for line in raw.replace("\r\n", "\n").split("\n")]
    # 連續空行壓成一行,整段前後的空行去掉
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    text = "\n".join(out).strip()
    return text or None


def _lines(cell) -> list[str]:
    """逐項清單。去掉開頭的 `●`,空行丟掉。"""
    text = _text(cell)
    if not text:
        return []
    return [
        _BULLET_RE.sub("", line).strip()
        for line in text.split("\n")
        if _BULLET_RE.sub("", line).strip()
    ]


def _teacher_name(cell) -> str | None:
    """教師姓名。同一格裡還有一個「教師諮商時間」的連結,要剔掉。"""
    cell = _without_links(cell)
    return _text(cell)


def _without_links(cell):
    """回傳一份拆掉所有 `<a>` 的複本。原節點不動 —— 同一格可能還要再讀。"""
    clone = BeautifulSoup(str(cell), "lxml")
    for anchor in clone.find_all("a"):
        anchor.decompose()
    return clone


def _email(cell) -> str | None:
    """Email。同一格裡有個 mailto 圖示連結,只取文字部分。"""
    text = _text(_without_links(cell))
    if not text:
        return None
    match = re.search(r"[^\s<>()]+@[^\s<>()]+", text)
    return match.group(0) if match else text


def _timestamp(text: str | None) -> str | None:
    """`2026-08-11 09:00:23` → `2026-08-11T01:00:23Z`。

    學校顯示的是台灣時間,但沒有標時區。全站其他時間戳都是 UTC,
    這裡一併轉過去,免得使用端拿到兩種基準的時間還得自己分辨。
    看不懂的格式原樣留著,不要為了漂亮而丟資料。
    """
    if not text:
        return None
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        log.warning("最後更新時間看不懂:%r,原樣保留", text)
        return text
    return (
        naive.replace(tzinfo=TAIPEI)
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _flexible_learning(cell) -> dict[str, Any] | None:
    """彈性學習(17-18 週)是一張巢狀表格,拆成 dict。"""
    table = cell.find("table")
    if table is None:
        text = _text(cell)
        return {"content": text} if text else None

    out: dict[str, Any] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) != 2:
            continue
        label = re.sub(r"\s+", "", clean(cells[0].get_text()) or "")
        key = _FLEX_FIELDS.get(label)
        if key is None:
            continue
        if key == "category":
            out[key] = _lines(cells[1])
        elif key == "hours":
            out[key] = _int(_text(cells[1]))
        else:
            out[key] = _text(cells[1])
    return out or None


def _int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None
