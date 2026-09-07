# 教室容量（座位數）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `Croom.jsp?format=-3` 上的「容量(座位數)」收進資料集，並在各學期的 `classrooms.json` 每筆補上 `capacity` 欄位。

**Architecture:** 新增一支純函式解析器 `parse_croom.py`；容量存在根目錄 `capacity.json`（代碼 → 現值，無沿革）；抓取由新的 `--with-capacity` 旗標驅動，只抓「不在狀態檔」或「超過重抓門檻」的教室；寫 `classrooms.json` 時從狀態檔補欄位，不發任何請求。每月由獨立的 `capacity.yml` 重抓一輪。

**Tech Stack:** Python 3.12、BeautifulSoup + lxml、pytest、GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-07-classroom-capacity-design.md`

## Global Constraints

以下取自 `reference.md`（當時叫 `plan.md`）的硬性規定與本專案既有慣例，**每個任務都適用**：

- **單執行緒**，不使用 `threading` / `asyncio` / `multiprocessing` 平行抓取。
- 每次請求後 sleep，**下限 0.5 秒**，不得調低。抓取一律走既有的 `Fetcher`。
- **測試全離線。** `tests/conftest.py` 的 `no_real_network` fixture 會讓任何發出真實 HTTP 請求的測試直接失敗，不要拆掉它。新樣本存成 `tests/fixtures/` 的 HTML。
- **解析器是純函式**：吃 HTML 字串 → 吐 dict，不發網路請求、不碰檔案系統。
- 讀不到的值一律 `None`，**不要填 0**。「沒讀到」和「零」是兩回事。
- repo 的檔案是 **LF**。用 Python 寫檔時 `.write_bytes(s.encode("utf-8").replace(b"\r\n", b"\n"))`。
- `schema_version` **維持 3**，不升版（README 承諾「新增欄位、新增端點不會升版」）。
- 空欄位在這個站台是**全形空白 U+3000**，用 `crawler.parse_util.clean()` 處理，不要只 `strip()`。
- HTML 一律用 `crawler.parse_util.soup_of()`（lxml）；這個站台的 `<tr>`/`<td>` 沒有收尾標籤，`html.parser` 會切錯表格。
- commit 訊息結尾要帶：
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```
- 執行測試用 `.venv/Scripts/python.exe -m pytest`（系統的 `python` 沒有 pytest）。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `crawler/parse_croom.py`（新增） | 解析 Croom.jsp 頁面 → `{name, full_name, capacity}`。純函式。 |
| `tests/fixtures/croom_page_real.html`（新增） | 實抓的教室頁樣本（code=48, 115-1）。 |
| `tests/test_parse_croom.py`（新增） | 解析器測試。 |
| `crawler/output.py`（修改） | 新增 `read_capacity()` / `write_capacity()`；`_write_classrooms()` 多收 `out_dir` 並補 `capacity` 欄位；`_endpoints()` 加一列。 |
| `crawler/main.py`（修改） | 新增 `classroom_targets()`、`select_capacity_targets()`、`crawl_capacity()` 與三個 CLI 旗標。 |
| `tests/test_capacity.py`（新增） | 狀態檔、挑選規則、抓取迴圈、寫檔的測試。 |
| `.github/workflows/capacity.yml`（新增） | 每月重抓一輪。 |
| `tests/test_workflow_schedule.py`（修改） | 加上 `capacity.yml` 的結構斷言。 |
| `README.md`（修改） | 端點表新增一列，並寫明語意限制。 |

---

### Task 1: 解析器

**Files:**
- Create: `crawler/parse_croom.py`
- Create: `tests/fixtures/croom_page_real.html`
- Test: `tests/test_parse_croom.py`

**Interfaces:**
- Consumes: `crawler.parse_util.soup_of`, `crawler.parse_util.clean`
- Produces: `parse_classroom(html: str) -> dict[str, Any]`，回傳三個鍵
  `{"name": str | None, "full_name": str | None, "capacity": int | None}`

**版面事實**（實抓確認）：第一張 `<table>` 有兩列 `<th>` 表頭，接著一列 `<td>` 資料，共 9 格：
`[0]` 教室簡稱、`[1]` 教室全名、`[2]` 容量(座位數)、`[3..8]` 日間／夜間／週末的節數與使用率。

- [ ] **Step 1: 存下 fixture**

把先前實抓的頁面存成 fixture。**這是這個功能唯一需要的真實樣本，之後全程離線。**

```bash
curl -s -m 40 -A "ntut-course-crawler/1.0 (+https://github.com/tntrock/ntut-course-crawler)" \
  "https://aps.ntut.edu.tw/course/tw/Croom.jsp?format=-3&year=115&sem=1&code=48" \
  -o tests/fixtures/croom_page_real.html
```

確認它含有「容量」與「50」：

```bash
grep -c "50" tests/fixtures/croom_page_real.html
```

- [ ] **Step 2: 寫會失敗的測試**

`tests/test_parse_croom.py`：

```python
"""解析教室頁(`Croom.jsp?format=-3`)。

只取容量。使用率與週課表刻意不解析 —— 見設計文件的「刻意不做」。
"""

from __future__ import annotations

import pytest

from crawler.parse_croom import parse_classroom
from tests.conftest import load_fixture


@pytest.fixture
def page():
    return load_fixture("croom_page_real.html")


class TestRealPage:
    def test_reads_capacity(self, page):
        assert parse_classroom(page)["capacity"] == 50

    def test_reads_both_names(self, page):
        parsed = parse_classroom(page)
        assert parsed["name"] == "三教307(e)"
        assert parsed["full_name"] == "第三教學大樓307室"


class TestBrokenPages:
    def test_missing_capacity_is_none_not_zero(self):
        """「讀不到」和「零個座位」是兩回事,填 0 會讓使用端算出無限大的使用率。"""
        html = (
            "<table border='1'><tr><th>教室簡稱</th></tr>"
            "<tr><td>三教307(e)</td><td>第三教學大樓307室</td><td>　</td></tr></table>"
        )
        assert parse_classroom(html)["capacity"] is None

    def test_non_numeric_capacity_is_none(self):
        html = (
            "<table border='1'><tr><th>教室簡稱</th></tr>"
            "<tr><td>某教室</td><td>某某室</td><td>不詳</td></tr></table>"
        )
        assert parse_classroom(html)["capacity"] is None

    def test_no_table_at_all(self):
        """學校改版或回了錯誤頁時,不要拋例外 —— 呼叫端要能記錯誤後繼續。"""
        parsed = parse_classroom("<html><body>查無資料</body></html>")
        assert parsed == {"name": None, "full_name": None, "capacity": None}

    def test_too_few_cells(self):
        html = "<table border='1'><tr><td>只有一格</td></tr></table>"
        assert parse_classroom(html)["capacity"] is None
```

- [ ] **Step 3: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_parse_croom.py -q
```

預期：`ModuleNotFoundError: No module named 'crawler.parse_croom'`

- [ ] **Step 4: 寫最小實作**

`crawler/parse_croom.py`：

```python
"""解析教室頁(`Croom.jsp?format=-3`)。

版面是一張表:兩列 `<th>` 表頭(含 rowspan/colspan),接著一列 `<td>` 資料 ——
教室簡稱、教室全名、容量(座位數),後面六格是日間/夜間/週末的節數與使用率。

**只取容量。** 使用率會隨課表變動、與 schedule.json 資訊重疊;週課表的內容
我們已經從課表頁拿到了。見設計文件的「刻意不做」。

實測 `year` / `sem` 只影響使用率與週課表,不影響容量 —— 打不存在的學年度
(year=199)仍然回傳正確容量。所以容量是教室主檔的屬性。
"""

from __future__ import annotations

import logging
from typing import Any

from .parse_util import clean, soup_of

log = logging.getLogger("crawler.parse_croom")

#: 資料列至少要有這幾格才讀得到容量(簡稱、全名、容量)。
_MIN_CELLS = 3

EMPTY: dict[str, Any] = {"name": None, "full_name": None, "capacity": None}


def parse_classroom(html: str) -> dict[str, Any]:
    """吃教室頁的 HTML,回傳 `{name, full_name, capacity}`。

    解析不出來時每個欄位都是 `None`,**不拋例外** —— 呼叫端要能記下錯誤
    然後繼續抓下一間,不能因為一間教室的版面怪就中斷整批。
    """
    table = soup_of(html).find("table")
    if table is None:
        log.warning("教室頁沒有表格,可能是錯誤頁或版面改了")
        return dict(EMPTY)

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < _MIN_CELLS:
            continue  # 表頭列只有 <th>,資料列才有 <td>
        return {
            "name": clean(cells[0].get_text()),
            "full_name": clean(cells[1].get_text()),
            "capacity": _to_int(clean(cells[2].get_text())),
        }

    log.warning("教室頁找不到資料列")
    return dict(EMPTY)


def _to_int(text: str | None) -> int | None:
    """數字讀不出來就回 None。**不要回 0** —— 「沒讀到」和「零個座位」
    是兩回事,填 0 會讓使用端算出無限大的使用率。"""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        log.warning("容量欄位不是數字:%r", text)
        return None
```

- [ ] **Step 5: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest tests/test_parse_croom.py -q
```

預期：`6 passed`

- [ ] **Step 6: Commit**

```bash
git add crawler/parse_croom.py tests/test_parse_croom.py tests/fixtures/croom_page_real.html
git commit -m "feat(parse): 解析教室頁的容量欄位

只取容量,使用率與週課表刻意不解析。解析不出來時回 None 而不是 0 ——
「沒讀到」和「零個座位」是兩回事。版面壞掉時不拋例外,讓呼叫端能記下
錯誤後繼續抓下一間。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 狀態檔讀寫

**Files:**
- Modify: `crawler/output.py`（在 `read_syllabus_state` 附近，約 541 行後）
- Test: `tests/test_capacity.py`

**Interfaces:**
- Consumes: `crawler.output._read_json`、`_write_json`、`_now`、`SCHEMA_VERSION`
- Produces:
  - `read_capacity(out_dir: Path) -> dict[str, dict[str, Any]]`
    回傳 `代碼 -> {"name", "full_name", "capacity", "checked_at"}`；檔案不存在回 `{}`
  - `write_capacity(out_dir: Path, classrooms: dict[str, dict[str, Any]], *, pretty: bool = False) -> None`

- [ ] **Step 1: 寫會失敗的測試**

`tests/test_capacity.py`（新檔）：

```python
"""教室容量:狀態檔、挑選規則、抓取迴圈、寫檔。

容量是教室主檔的屬性,與學期無關(實測 year=199 仍回傳正確容量)。
所以狀態檔以教室代碼為主鍵,不分學期;每月重抓一輪讓現值保持正確。
"""

from __future__ import annotations

import json

from crawler.output import read_capacity, write_capacity


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestCapacityState:
    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert read_capacity(tmp_path) == {}

    def test_round_trip(self, tmp_path):
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2026-09-07T02:00:00Z"},
        })
        assert read_capacity(tmp_path)["48"]["capacity"] == 50

    def test_envelope_has_count_and_schema(self, tmp_path):
        write_capacity(tmp_path, {
            "48": {"name": "A", "full_name": "AA", "capacity": 50,
                   "checked_at": "2026-09-07T02:00:00Z"},
            "9": {"name": "B", "full_name": "BB", "capacity": None,
                  "checked_at": "2026-09-07T02:00:00Z"},
        })
        payload = read(tmp_path / "capacity.json")
        assert payload["schema_version"] == 3
        assert payload["classroom_count"] == 2
        assert payload["generated_at"].endswith("Z")

    def test_unreadable_capacity_stays_null_not_zero(self, tmp_path):
        write_capacity(tmp_path, {
            "9": {"name": "B", "full_name": "BB", "capacity": None,
                  "checked_at": "2026-09-07T02:00:00Z"},
        })
        assert read_capacity(tmp_path)["9"]["capacity"] is None

    def test_corrupt_file_is_empty_not_an_exception(self, tmp_path):
        """壞掉的狀態檔不該讓整批抓取無法啟動 —— 最壞就是重抓一輪。"""
        (tmp_path / "capacity.json").write_text("{壞掉", encoding="utf-8")
        assert read_capacity(tmp_path) == {}
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`ImportError: cannot import name 'read_capacity' from 'crawler.output'`

- [ ] **Step 3: 寫最小實作**

在 `crawler/output.py` 的 `read_syllabus_state()` 之前插入：

```python
# --------------------------------------------------------------------------
# 教室容量
# --------------------------------------------------------------------------
def read_capacity(out_dir: Path) -> dict[str, dict[str, Any]]:
    """讀 `capacity.json`,回傳 代碼 → {name, full_name, capacity, checked_at}。

    檔案不存在或壞掉時回空 dict —— 最壞的結果只是重抓一輪(445 頁、約 9 分鐘),
    不該讓整批抓取無法啟動。
    """
    payload = _read_json(Path(out_dir) / "capacity.json") or {}
    classrooms = payload.get("classrooms")
    return classrooms if isinstance(classrooms, dict) else {}


def write_capacity(
    out_dir: Path, classrooms: dict[str, dict[str, Any]], *, pretty: bool = False
) -> None:
    """寫 `capacity.json`。

    **不留改建沿革** —— 值變了就直接覆蓋。使用端要的是「現在幾個座位」,
    而學校本來就不保留歷史容量(實測改建後所有學期都會顯示新值)。
    """
    _write_json(
        Path(out_dir) / "capacity.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "classroom_count": len(classrooms),
            "classrooms": classrooms,
        },
        pretty,
    )
```

- [ ] **Step 4: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`5 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/output.py tests/test_capacity.py
git commit -m "feat(output): capacity.json 的讀寫

代碼是主鍵,不分學期 —— 容量是教室主檔的屬性。檔案壞掉時回空 dict 而不是
拋例外,最壞就是重抓一輪。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 挑選規則

**Files:**
- Modify: `crawler/main.py`（常數放在 `DEFAULT_MAX_SYLLABUS` 附近，約 83 行後；函式放在 `select_syllabus_targets` 附近）
- Test: `tests/test_capacity.py`（追加）

**Interfaces:**
- Consumes: `crawler.output.read_capacity`
- Produces:
  - `DEFAULT_CAPACITY_REFRESH_AFTER = 480.0`（小時）
  - `DEFAULT_MAX_CAPACITY = 0`（0 = 不限）
  - `classroom_targets(out_dir: Path) -> dict[str, tuple[str, int, int]]`
    掃各學期的 `classrooms.json`，回傳 `代碼 -> (教室名, 最新出現的 year, sem)`
  - `select_capacity_targets(known, targets, *, limit=None, refresh_after=DEFAULT_CAPACITY_REFRESH_AFTER, now=None) -> list[str]`

- [ ] **Step 1: 寫會失敗的測試**

追加到 `tests/test_capacity.py`：

```python
from datetime import datetime, timedelta, timezone

from crawler.main import (
    DEFAULT_CAPACITY_REFRESH_AFTER,
    classroom_targets,
    select_capacity_targets,
)


def write_semester_classrooms(out_dir, semester, entries):
    """造出一個學期的 classrooms.json(只放測試需要的欄位)。"""
    d = out_dir / semester
    d.mkdir(parents=True, exist_ok=True)
    (d / "classrooms.json").write_text(
        json.dumps({"schema_version": 3, "classrooms": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestClassroomTargets:
    def test_collects_codes_from_every_semester(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [{"id": "48", "name": "三教307"}])
        write_semester_classrooms(tmp_path, "110-1", [{"id": "9", "name": "共同301"}])
        targets = classroom_targets(tmp_path)
        assert set(targets) == {"48", "9"}

    def test_uses_the_newest_semester_a_code_appears_in(self, tmp_path):
        """用最新出現過的學期去抓 —— 那個頁面確定存在。"""
        write_semester_classrooms(tmp_path, "110-1", [{"id": "48", "name": "三教307"}])
        write_semester_classrooms(tmp_path, "115-1", [{"id": "48", "name": "三教307"}])
        assert classroom_targets(tmp_path)["48"] == ("三教307", 115, 1)

    def test_entries_without_a_code_are_skipped(self, tmp_path):
        """沒有代碼就組不出 URL,抓不了。"""
        write_semester_classrooms(tmp_path, "115-1", [{"id": None, "name": "未知"}])
        assert classroom_targets(tmp_path) == {}


class TestSelectCapacityTargets:
    def targets(self):
        return {"48": ("三教307", 115, 1), "9": ("共同301", 115, 1)}

    def test_unknown_codes_are_selected(self):
        assert set(select_capacity_targets({}, self.targets())) == {"48", "9"}

    def test_fresh_entries_are_skipped(self):
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        known = {
            "48": {"capacity": 50, "checked_at": "2026-09-06T00:00:00Z"},
            "9": {"capacity": 30, "checked_at": "2026-09-06T00:00:00Z"},
        }
        assert select_capacity_targets(known, self.targets(), now=now) == []

    def test_stale_entries_are_refetched(self):
        """容量會因改建而變,凍結的話新值永遠抓不到。"""
        now = datetime(2026, 9, 7, tzinfo=timezone.utc)
        old = (now - timedelta(hours=DEFAULT_CAPACITY_REFRESH_AFTER + 1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        known = {"48": {"capacity": 50, "checked_at": old}}
        assert select_capacity_targets(known, self.targets(), now=now) == ["48", "9"]

    def test_limit_splits_the_work(self):
        picked = select_capacity_targets({}, self.targets(), limit=1)
        assert len(picked) == 1

    def test_unparsable_checked_at_is_treated_as_stale(self):
        """時間讀不懂就當作該重抓 —— 最壞是多抓一次,不會漏抓。"""
        known = {"48": {"capacity": 50, "checked_at": "壞掉"}}
        assert "48" in select_capacity_targets(known, self.targets())
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`ImportError: cannot import name 'classroom_targets' from 'crawler.main'`

- [ ] **Step 3: 寫最小實作**

在 `crawler/main.py` 的常數區（`DEFAULT_MAX_SYLLABUS = 0` 之後）加：

```python
#: 教室容量隔多久重抓一次。**刻意小於一個月** —— 排程是每月 1 號,但二月
#: 只有 28 天,加上 Actions 的 cron 實測常延遲 2~4 小時,門檻若設 30 天,
#: 二月那輪會因為「還沒過 30 天」而整批跳過。
DEFAULT_CAPACITY_REFRESH_AFTER = 480.0

#: 一次最多抓幾間教室(0 = 不限,全部 445 間約 9 分鐘)。
DEFAULT_MAX_CAPACITY = 0
```

在 `select_syllabus_targets()` 附近加：

```python
def classroom_targets(out_dir: Path) -> dict[str, tuple[str, int, int]]:
    """掃過每個學期的 `classrooms.json`,列出所有教室代碼要用哪個學期去抓。

    用**該教室最新出現過的學期** —— 那個頁面確定存在。由實測可知 year/sem
    不影響容量值(打不存在的 year=199 仍回傳正確容量),所以選哪個學期只影響
    「頁面在不在」,不影響資料。
    """
    targets: dict[str, tuple[str, int, int]] = {}
    for path in sorted(Path(out_dir).glob("*/classrooms.json")):
        match = re.fullmatch(r"(\d+)-([12])", path.parent.name)
        if not match:
            continue
        year, sem = int(match.group(1)), int(match.group(2))
        payload = _read_json_or_empty(path)
        for entry in payload.get("classrooms", []):
            code = entry.get("id")
            if not code:
                continue  # 沒有代碼就組不出 URL
            previous = targets.get(code)
            if previous is None or (year, sem) > (previous[1], previous[2]):
                targets[code] = (entry.get("name") or code, year, sem)
    return targets


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("讀不到或解析不了 %s,略過", path)
        return {}


def select_capacity_targets(
    known: dict[str, dict[str, Any]],
    targets: dict[str, tuple[str, int, int]],
    *,
    limit: int | None = None,
    refresh_after: float = DEFAULT_CAPACITY_REFRESH_AFTER,
    now: datetime | None = None,
) -> list[str]:
    """挑出這次要抓的教室代碼。

    兩條規則:不在狀態檔的要抓;超過重抓門檻的要重抓。**沒有永久凍結** ——
    容量會因改建而變,凍結的話新值永遠抓不到。
    """
    now = now or datetime.now(timezone.utc)
    picked: list[str] = []
    for code in sorted(targets, key=lambda c: (len(c), c)):
        entry = known.get(code)
        if entry is not None:
            stamp = _parse_stamp(entry.get("checked_at"))
            # 時間讀不懂就當作該重抓 —— 最壞是多抓一次,不會漏抓
            if stamp is not None:
                age = (now - stamp).total_seconds() / 3600
                if age < refresh_after:
                    continue
        picked.append(code)
        if limit and len(picked) >= limit:
            break
    return picked
```

若 `main.py` 還沒有 `_parse_stamp`，一併加上（放在 `select_capacity_targets` 之前）：

```python
def _parse_stamp(raw: Any) -> datetime | None:
    """把 `2026-09-07T02:00:00Z` 解析成 aware datetime。看不懂就回 None。"""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
```

確認 `main.py` 檔頭已 import `re`、`json`、`Path`、`datetime`/`timezone`、`Any`；缺的補上。

- [ ] **Step 4: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`14 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/main.py tests/test_capacity.py
git commit -m "feat(capacity): 教室清單與挑選規則

用該教室最新出現過的學期去抓,那個頁面確定存在。重抓門檻預設 20 天,
刻意小於一個月 —— 排程是每月 1 號而二月只有 28 天,設 30 天會讓二月
整批跳過。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 抓取迴圈與 CLI 旗標

**Files:**
- Modify: `crawler/main.py`
- Test: `tests/test_capacity.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `parse_classroom`、Task 2 的 `read_capacity`/`write_capacity`、Task 3 的 `classroom_targets`/`select_capacity_targets`、既有的 `_classroom_url`（在 `output.py`，需 export）
- Produces: `crawl_capacity(fetcher, out_dir, *, limit=None, refresh_after=DEFAULT_CAPACITY_REFRESH_AFTER, pretty=False) -> dict[str, int]`
  回傳 `{"fetched": N, "changed": M, "failed": K}`

- [ ] **Step 1: 寫會失敗的測試**

追加到 `tests/test_capacity.py`：

```python
from crawler.main import crawl_capacity
from tests.conftest import load_fixture


class FakeCroomFetcher:
    """依 code 回傳教室頁。`fail_on` 裡的代碼會拋例外。"""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.urls = []
        self.delay = 1.0

    def fetch(self, url, *, params=None):
        self.urls.append(url)
        for code in self.fail_on:
            if f"code={code}" in url:
                raise RuntimeError(f"模擬 {code} 抓取失敗")
        return load_fixture("croom_page_real.html")


class TestCrawlCapacity:
    def prepare(self, tmp_path):
        write_semester_classrooms(tmp_path, "115-1", [
            {"id": "48", "name": "三教307(e)"},
            {"id": "9", "name": "共同301"},
        ])

    def test_writes_capacity_for_every_classroom(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path)
        assert stats["fetched"] == 2
        state = read_capacity(tmp_path)
        assert state["48"]["capacity"] == 50
        assert state["48"]["checked_at"].endswith("Z")

    def test_second_run_skips_fresh_entries(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcher(), tmp_path)
        fetcher = FakeCroomFetcher()
        stats = crawl_capacity(fetcher, tmp_path)
        assert stats["fetched"] == 0
        assert fetcher.urls == [], "沒有到期就不該再打學校"

    def test_a_failure_does_not_stop_the_batch(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(fail_on={"48"}), tmp_path)
        assert stats["failed"] == 1
        assert stats["fetched"] == 1
        assert "9" in read_capacity(tmp_path)
        assert "48" not in read_capacity(tmp_path), "失敗的不進狀態檔,下次會重試"

    def test_failure_is_recorded_in_errors_json(self, tmp_path):
        self.prepare(tmp_path)
        crawl_capacity(FakeCroomFetcher(fail_on={"48"}), tmp_path)
        errors = read(tmp_path / "errors.json")["errors"]
        assert any(e["stage"] == "classroom" and e["classroom_id"] == "48"
                   for e in errors)

    def test_limit_splits_the_work(self, tmp_path):
        self.prepare(tmp_path)
        stats = crawl_capacity(FakeCroomFetcher(), tmp_path, limit=1)
        assert stats["fetched"] == 1
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`ImportError: cannot import name 'crawl_capacity'`

- [ ] **Step 3: 先把 URL 組裝函式改成公開**

`crawler/output.py` 目前的 `_classroom_url()` 是私有的，抓取端要用。改名為 `classroom_url()` 並更新 `_write_classrooms()` 裡唯一的呼叫處：

```python
def classroom_url(code: str, year: int, sem: int) -> str:
    return f"{BASE_URL}Croom.jsp?format=-3&year={year}&sem={sem}&code={code}"
```

```bash
grep -rn "_classroom_url" crawler/    # 應該只剩定義處與 _write_classrooms 各一處
```

- [ ] **Step 4: 寫最小實作**

在 `crawler/main.py` 加：

```python
def crawl_capacity(
    fetcher: "Fetcher",
    out_dir: Path,
    *,
    limit: int | None = None,
    refresh_after: float = DEFAULT_CAPACITY_REFRESH_AFTER,
    pretty: bool = False,
) -> dict[str, int]:
    """抓教室容量,更新 `capacity.json`。

    一次失敗只影響一間教室:記進 errors.json、不寫進狀態檔,下次執行會自動
    重試(因為它仍然「不在狀態檔裡」)。**不要讓一間教室的版面問題中斷整批。**
    """
    out_dir = Path(out_dir)
    known = read_capacity(out_dir)
    targets = classroom_targets(out_dir)
    picked = select_capacity_targets(
        known, targets, limit=limit, refresh_after=refresh_after
    )
    if not picked:
        log.info("教室容量:沒有需要抓的(共 %d 間已在狀態檔)", len(known))
        return {"fetched": 0, "changed": 0, "failed": 0}

    log.info("教室容量:這次抓 %d / %d 間", len(picked), len(targets))
    fetched = changed = failed = 0
    errors: list[dict[str, Any]] = []

    for index, code in enumerate(picked, start=1):
        name, year, sem = targets[code]
        url = classroom_url(code, year, sem)
        if index % 50 == 0:
            log.info("教室容量:%d / %d", index, len(picked))
        try:
            parsed = parse_classroom(fetcher.fetch(url))
        except Exception as exc:
            log.error("教室 %s (%s) 抓取失敗:%s", name, code, exc)
            errors.append(
                {
                    "stage": "classroom",
                    "classroom_id": code,
                    "classroom_name": name,
                    "url": url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            failed += 1
            continue

        before = (known.get(code) or {}).get("capacity")
        if parsed["capacity"] is None:
            errors.append(
                {
                    "stage": "classroom",
                    "classroom_id": code,
                    "classroom_name": name,
                    "url": url,
                    "error": "頁面抓得到但讀不出容量",
                }
            )
        elif before is not None and before != parsed["capacity"]:
            log.info("教室 %s 容量從 %s 變成 %s", name, before, parsed["capacity"])
            changed += 1

        known[code] = {
            "name": parsed["name"] or name,
            "full_name": parsed["full_name"],
            "capacity": parsed["capacity"],
            "checked_at": _utc_now(),
        }
        fetched += 1

    write_capacity(out_dir, known, pretty=pretty)
    if errors:
        append_errors(out_dir, errors, pretty=pretty)
    return {"fetched": fetched, "changed": changed, "failed": failed}
```

`errors.json` 的追加：若 `output.py` 還沒有可重用的入口，新增

```python
def append_errors(
    out_dir: Path, errors: list[dict[str, Any]], *, pretty: bool = False
) -> None:
    """把錯誤追加進 errors.json,保留其他學年期既有的錯誤。"""
    path = Path(out_dir) / "errors.json"
    existing = _read_json(path) or {}
    kept = list(existing.get("errors", []))
    payload = dict(existing)
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "error_count": len(kept) + len(errors),
            "errors": kept + errors,
        }
    )
    _write_json(path, payload, pretty)
```

在 `main.py` 的 argparse 區加三個旗標（緊接在大綱那三個之後）：

```python
    parser.add_argument(
        "--with-capacity",
        action="store_true",
        help="順便抓教室容量(一間教室一頁,全部約 445 間、9 分鐘,預設關閉)",
    )
    parser.add_argument(
        "--max-capacity",
        type=int,
        default=DEFAULT_MAX_CAPACITY,
        metavar="N",
        help="這次最多抓幾間教室(預設 0 = 不限)",
    )
    parser.add_argument(
        "--capacity-refresh-after",
        type=float,
        default=DEFAULT_CAPACITY_REFRESH_AFTER,
        metavar="HOURS",
        help=f"教室容量隔多久重抓(預設 {DEFAULT_CAPACITY_REFRESH_AFTER:.0f} 小時)",
    )
```

在 `main()` 的抓取流程尾端（`_print_summary` 之前）加：

```python
    if args.with_capacity:
        crawl_capacity(
            fetcher,
            args.out,
            limit=args.max_capacity or None,
            refresh_after=args.capacity_refresh_after,
            pretty=args.pretty,
        )
```

補上 import：`from .parse_croom import parse_classroom`、以及 `output` 的
`read_capacity, write_capacity, classroom_url, append_errors`。

- [ ] **Step 5: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -q
```

預期：`20 passed`

- [ ] **Step 6: 跑整套測試，確認沒有弄壞別的**

```bash
.venv/Scripts/python.exe -m pytest -q
```

預期：全部通過（原有 352 + 本計畫新增）

- [ ] **Step 7: Commit**

```bash
git add crawler/main.py crawler/output.py tests/test_capacity.py
git commit -m "feat(capacity): 抓取迴圈與 CLI 旗標

一間教室失敗只記進 errors.json、不寫狀態檔,下次自動重試 —— 不讓單一
教室的版面問題中斷整批。容量讀不出來時照樣記 checked_at 並留一筆錯誤,
避免版面改了卻無聲地一直重抓。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 把 capacity 補進 classrooms.json

**Files:**
- Modify: `crawler/output.py:104-132`（`write_outputs`）與 `:428`（`_write_classrooms`）
- Test: `tests/test_capacity.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `read_capacity`
- Produces: `_write_classrooms(result, semester_dir, out_dir, pretty)` — **多一個 `out_dir` 參數**

- [ ] **Step 1: 寫會失敗的測試**

追加到 `tests/test_capacity.py`：

```python
from crawler.output import write_outputs
from tests.test_main import FakeFetcher  # noqa: F401
from crawler.main import crawl


class TestClassroomsGetCapacity:
    def result(self):
        return crawl(FakeFetcher(), 115, 1, only_departments=["59"])

    def test_capacity_is_stamped_from_the_state_file(self, tmp_path):
        write_capacity(tmp_path, {
            "48": {"name": "三教307(e)", "full_name": "第三教學大樓307室",
                   "capacity": 50, "checked_at": "2026-09-07T02:00:00Z"},
        })
        write_outputs(self.result(), tmp_path)
        entries = read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        assert all("capacity" in e for e in entries), "每一筆都要有這個欄位"

    def test_unknown_classrooms_get_null_not_zero(self, tmp_path):
        write_outputs(self.result(), tmp_path)   # 沒有狀態檔
        entries = read(tmp_path / "115-1" / "classrooms.json")["classrooms"]
        assert all(e["capacity"] is None for e in entries)

    def test_writing_does_not_fetch_anything(self, tmp_path):
        """補欄位只是查表,不該發任何請求 —— no_real_network 會抓到違規。"""
        write_capacity(tmp_path, {"48": {"name": "A", "full_name": "AA",
                                         "capacity": 50,
                                         "checked_at": "2026-09-07T02:00:00Z"}})
        write_outputs(self.result(), tmp_path)   # 不拋例外就算過
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -k Capacity -q
```

預期：`KeyError: 'capacity'`

- [ ] **Step 3: 寫最小實作**

`crawler/output.py`：把 `_write_classrooms` 的簽名與內容改成

```python
def _write_classrooms(
    result: "CrawlResult", semester_dir: Path, out_dir: Path, pretty: bool
) -> None:
    """教室 → 課號。可以拿來找空教室,或看某間教室排了什麼課。

    `capacity` 取自根目錄的 `capacity.json`,**查表而已,不發任何請求**。
    狀態檔沒有該代碼時是 `None` —— 「還沒抓過」和「零個座位」是兩回事。

    注意:容量與學期無關(實測 year/sem 不影響該欄位),所以歷史學期的
    `capacity` 反映的是**最近一次觀測值**,不是那個學期當時的容量。
    學校不保留歷史容量,這點無法補救,README 有寫明。
    """
    capacity = read_capacity(out_dir)
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
                "capacity": (capacity.get(code) or {}).get("capacity"),
                "course_count": len(ids),
                "course_ids": sorted(ids),
                "url": classroom_url(safe, result.year, result.sem) if safe else None,
            }
        )

    payload = _envelope(result)
    payload["classroom_count"] = len(classrooms)
    payload["classrooms"] = classrooms
    _write_json(semester_dir / "classrooms.json", payload, pretty)
```

並把 `write_outputs()` 裡的呼叫改成：

```python
    _write_classrooms(result, semester_dir, out_dir, pretty)
```

- [ ] **Step 4: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest -q
```

預期：全部通過

- [ ] **Step 5: Commit**

```bash
git add crawler/output.py tests/test_capacity.py
git commit -m "feat(output): classrooms.json 每筆補上 capacity

查 capacity.json 而已,寫檔時不發任何請求。狀態檔沒有的代碼填 null 而不是
0 —— 「還沒抓過」和「零個座位」是兩回事。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: workflow

**Files:**
- Create: `.github/workflows/capacity.yml`
- Modify: `tests/test_workflow_schedule.py`

**Interfaces:**
- Consumes: Task 4 的 `--with-capacity` 旗標

- [ ] **Step 1: 寫會失敗的測試**

追加到 `tests/test_workflow_schedule.py`：

```python
CAPACITY = ROOT / ".github" / "workflows" / "capacity.yml"


class TestCapacityWorkflow:
    def workflow(self) -> dict:
        return yaml.safe_load(CAPACITY.read_text(encoding="utf-8"))

    def test_runs_monthly_and_on_demand(self) -> None:
        triggers = self.workflow()[True]     # yaml 把 `on:` 解析成布林 True
        assert triggers["schedule"][0]["cron"] == "0 2 1 * *"
        assert "workflow_dispatch" in triggers

    def test_shares_the_crawl_concurrency_group(self) -> None:
        """不可以跟其他抓取同時對學校發請求。"""
        assert self.workflow()["concurrency"]["group"] == "crawl"

    def test_actually_asks_for_capacity(self) -> None:
        assert "--with-capacity" in CAPACITY.read_text(encoding="utf-8")
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workflow_schedule.py -q
```

預期：`FileNotFoundError: ...capacity.yml`

- [ ] **Step 3: 寫最小實作**

`.github/workflows/capacity.yml`。

**先開著 `.github/workflows/syllabus.yml` 逐段對照**，把下列步驟**原封不動抄過來**
（只有 `Crawl capacity` 那一步是新寫的）：

1. `- uses: actions/checkout@v7`
2. `- uses: actions/setup-python@v7`（`python-version: '3.12'`、`cache: pip`）
3. `- run: pip install -r requirements-dev.txt`
4. `- name: Test`（`run: pytest -q`）
5. `- name: Restore shared index files`（整段 shell，含還原 7 個共用檔的迴圈）
6. **`- name: Crawl capacity`（新寫，見下）**
7. `- name: Record this run`（含 `if: always()`）
8. `- name: Add landing pages`（含 `if: always()`、`cp -R web/. data/`）
9. `- name: Upload data as artifact`（含 `if: always()`）
10. `- name: Publish to gh-pages`（含 `if: always()`、`keep_files: true`）

**不要憑記憶重寫這些步驟** —— 尤其 `Restore shared index files` 少還原一個檔就會
讓整批索引被覆蓋掉，而 `Publish` 少了 `keep_files` 會把整個站台清掉。

關鍵設定：

```yaml
name: capacity

# 教室容量。實測容量來自教室主檔、與學期無關(打不存在的 year=199 仍回傳
# 正確容量),所以不必每學期抓 —— 每月重抓一輪讓現值在改建後保持正確就夠。
#
# 排在同一天 06:00 的整包重建**之前**,讓重建帶著最新的容量發布。
# 那個時段沒有 4 小時排程的班次(00:00 與 04:00 之間),不會自己撞自己。
on:
  schedule:
    - cron: '0 2 1 * *'
  workflow_dispatch:
    inputs:
      max_capacity:
        description: '這次最多抓幾間教室(留空 = 不限,全部約 445 間、9 分鐘)'
        default: ''
      delay:
        description: '每次請求後的延遲秒數(下限 0.5)'
        default: '1.0'

permissions:
  contents: write

# 與 crawl.yml 共用:容量重抓不可以跟日常抓取同時對學校發請求。
concurrency:
  group: crawl
  cancel-in-progress: false

jobs:
  capacity:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      # ... checkout / setup-python / pip install / pytest / Restore shared index files
      # (照抄 syllabus.yml)

      # 失敗就等 30 分鐘整批重來,最多 3 次 —— 跟 crawl / syllabus 同一套理由:
      # crawler 內部的重試治得了線路抖動,治不了學校端幾十分鐘的中斷。
      # 重試很便宜:已經寫進 capacity.json 的教室不會再被選中(挑選規則會
      # 跳過未到期的),所以重試只補剩下的,不是從頭再跑一遍。
      - name: Crawl capacity
        env:
          MAX_CAPACITY: ${{ inputs.max_capacity }}
          DELAY: ${{ inputs.delay || '1.0' }}
          MAX_ATTEMPTS: '3'
          RETRY_WAIT_SECONDS: '1800'
        run: |
          # 這一段的重試迴圈同樣照抄 syllabus.yml 的 Crawl 步驟,
          # 只把指令換成下面這行:
          python -m crawler.main --with-capacity --out data \
            ${MAX_CAPACITY:+--max-capacity "$MAX_CAPACITY"} \
            --delay "${DELAY:-1.0}" \
            --run-summary "$RUNNER_TEMP/run-summary.json"
```

- [ ] **Step 4: 執行測試，確認全部通過**

```bash
.venv/Scripts/python.exe -m pytest tests/test_workflow_schedule.py -q
.venv/Scripts/python.exe -c "import yaml; yaml.safe_load(open('.github/workflows/capacity.yml', encoding='utf-8')); print('yaml ok')"
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/capacity.yml tests/test_workflow_schedule.py
git commit -m "feat(ci): 每月重抓一輪教室容量

排在同一天整包重建之前,讓重建帶著最新容量發布。共用 crawl 的 concurrency
群組,不與其他抓取同時對學校發請求。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 文件與端點清單

**Files:**
- Modify: `crawler/output.py`（`_endpoints()`，約 1288 行）
- Modify: `README.md`
- Test: `tests/test_capacity.py`（追加）

- [ ] **Step 1: 寫會失敗的測試**

```python
class TestEndpointsListed:
    def test_capacity_is_advertised_in_meta(self, tmp_path):
        """meta.json 的 endpoints 是使用者發現新資料的唯一途徑。"""
        write_outputs(crawl(FakeFetcher(), 115, 1, only_departments=["59"]), tmp_path)
        paths = [e["path"] for e in read(tmp_path / "meta.json")["endpoints"]]
        assert "capacity.json" in paths
```

- [ ] **Step 2: 執行測試，確認它失敗**

```bash
.venv/Scripts/python.exe -m pytest tests/test_capacity.py -k Endpoints -q
```

預期：`AssertionError`

- [ ] **Step 3: 加進端點清單**

`crawler/output.py` 的 `_endpoints()` 回傳的 list 裡，在 `syllabus.json` 那列附近加：

```python
        {"path": "capacity.json", "description": "教室容量(座位數)"},
```

- [ ] **Step 4: 執行測試，確認通過**

```bash
.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 5: 更新 README**

在端點表新增一列：

```markdown
| `capacity.json` | 教室容量（座位數） | 約 25 KB |
```

並在 `{semester}/classrooms.json` 的說明附近加一段：

```markdown
#### 教室容量

`capacity.json` 是教室代碼 → 容量的對照表，各學期的 `classrooms.json` 每筆也
直接帶 `capacity` 欄位，不必再查一次。

> **容量與學期無關。** 實測 `Croom.jsp` 的 `year` / `sem` 參數只影響使用率與
> 週課表，容量欄位不受影響 —— 打一個不存在的學年度（`year=199`）仍然回傳
> 正確容量。所以學校**不保留歷史容量**：教室改建後，所有學期都會顯示新值。
>
> 因此各學期 `classrooms.json` 的 `capacity` 反映的是**最近一次觀測值**，
> 不是那個學期當時的容量。抓不到時是 `null`，不是 `0`。
>
> 每月重抓一輪，`capacity.json` 的 `checked_at` 是該筆最後一次確認的時間。
```

- [ ] **Step 6: Commit**

```bash
git add crawler/output.py README.md tests/test_capacity.py
git commit -m "docs: 教室容量的端點與語意限制

meta.json 的 endpoints 是使用者發現新資料的唯一途徑,漏了就等於沒發布。
README 寫明容量與學期無關 —— 改建後舊學期的數字也會跟著變,那是來源端
的限制,不是 bug。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 完成後的驗收

```bash
# 1. 全部測試
.venv/Scripts/python.exe -m pytest -q

# 2. 五支 workflow 的 YAML
.venv/Scripts/python.exe -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{f}.yml', encoding='utf-8')) for f in ['crawl','syllabus','backfill','capacity','test']]; print('ok')"

# 3. 確認測試沒有偷打學校(no_real_network 會擋,但再確認一次沒有新的真實請求)
grep -rn "aps.ntut.edu.tw" tests/ | grep -v fixtures
```

第一次上線時**先用 `-f max_capacity=5` 手動 dispatch 一次**，確認五間教室的容量寫進 `capacity.json` 且數值合理，再跑全量 445 間。
