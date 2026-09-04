# 北科課程系統爬蟲 + 靜態 API — 開發規劃書

> 這份文件是本專案的完整開發規格與偵察紀錄,同時也是決策的存檔。開發依階段順序推進,每個階段有明確的驗收條件,未通過不進入下一階段。

> **文件狀態(2026-09-03 更新)**:Phase 0 偵察已完成,本文件已依實測結果修正。
>
> - ✅ = 已由真實 fixture 驗證的事實
> - ❓ = 尚未驗證的推測,**不可當成已知條件**,遇到時要先確認再實作
>
> **Phase 0 推翻的原始假設(重要)**:
>
> 1. 編碼不是 Big5 / cp950,是 **UTF-8**
> 2. 階層比原本畫的**多一層**:`format=-2` → `-3` → `-4` 才是課程列表
> 3. 總覽頁給的分組是**學院**,不是原本模型裡的「學制」
> 4. Python 內建 SSL 驗證會擋掉學校憑證,需用 `truststore`(見 §1.5)

---

## 0. 專案目標

建立一個**完全免費、全雲端**的爬蟲與資料發布系統:

1. 定期爬取國立臺北科技大學課程系統的公開課程資料
2. 轉換為結構化 JSON
3. 透過 GitHub Pages 發布為靜態 API,供他人使用

**技術棧**:Python 3.12 + GitHub Actions + GitHub Pages
**不使用**:無頭瀏覽器(Selenium/Playwright)、外部資料庫、任何付費服務

---

## 1. 目標網站資訊

### 1.1 基本資料

- 系統首頁:`https://aps.ntut.edu.tw/course/tw/course.jsp`
- 技術特徵:傳統 JSP + 表格排版,**無前端渲染**,可用純 HTTP request 抓取 ✅
- 編碼:**UTF-8** ✅
  - 回應標頭為 `Content-Type: text/html; charset=UTF-8`,實際內容也確實是 UTF-8
  - 頁面內有一行 `<meta charset=UTF-8>` 但**被 HTML 註解包住**,不要依賴它判斷
  - 直接 `content.decode('utf-8')` 即可,不需要 `errors='replace'` 降級

### 1.2 已知的 URL 模式 ✅

(`year` = 學年度,`sem` = 學期)

| 用途 | URL 模式 |
|---|---|
| 學院 / 單位總覽(主要入口) | `/course/tw/Subj.jsp?format=-2&year=115&sem=1` |
| 單位底下的班級列表 | `/course/tw/Subj.jsp?format=-3&year=115&sem=1&code={單位代碼}` |
| **實際課程列表** | `/course/tw/Subj.jsp?format=-4&year=115&sem=1&code={班級代碼}` |
| 教學大綱 | `/course/tw/ShowSyllabus.jsp?snum={課號}&code={教師代碼}` |
| 教師授課時數 | `/course/tw/Teach.jsp?format=-3&year=115&sem=1&code={教師代碼}` |
| 教室使用情形 | `/course/tw/Croom.jsp?format=-3&year=115&sem=1&code={教室代碼}` |
| 必選修符號說明(課程標準) | `/course/tw/Cprog.jsp?format=-5` ✅ 對照表見 Phase 3 |

**MVP 只做 `Subj.jsp` 這條線**,其他先不碰。

### 1.3 實際階層結構 ✅

```
Subj.jsp?format=-2&year=115&sem=1                ← 學院 / 行政單位總覽
  └─ Subj.jsp?format=-3&...&code={單位代碼}       ← 該單位底下的班級列表
       └─ Subj.jsp?format=-4&...&code={班級代碼}   ← 課程列表(每列一門課)
            └─ ShowSyllabus.jsp?snum=&code=       ← 教學大綱(可選,量大)
```

實例(資工系):

```
format=-2                    → 電資學院 底下有「資工系」,code=59
format=-3&code=59            → 資工一(3718) / 資工二(3138) / 資工三(3032) / 資工四(2915) / 資工所(3743)
format=-4&code=2915          → 資工四的課程表格
ShowSyllabus.jsp?snum=364893&code=12095  → 「數位影像處理」的教學大綱
```

**三個必須注意的陷阱**:

1. **總覽頁第一個連結不是系所**。`format=-2` 的第一列是行政單位(教務處 `code=01`、體育室 `code=10`、通識中心 `code=14`、師資培育中心 `code=62`、校院級課程 `code=AA`),往下爬只會拿到「遠距教學班」「輔導課程」這類班群。真正的系所在後面的學院表格裡。開發時**不要拿第一個連結當樣本**。
2. **`code` 在 `-3` → `-4` 之間會換一組**。系所代碼是 `59`(兩碼英數),班級代碼是 `2915`(四位數字),兩者無法互推,是伺服器另配的 ID。**一定要從 `-3` 頁面解析出完整連結**,不可自己拼 URL。
3. **同一門課會出現在多個班級頁**。備註欄常見「資工四和資工所合開」,代表同一課號會在兩個 `format=-4` 頁面各出現一次。輸出前要**依課號去重**,並保留它隸屬的所有班級。

### 1.4 ⚠️ 重要限制:必須限速

參考既有專案 [gnehs/ntut-course-crawler-node](https://github.com/gnehs/ntut-course-crawler-node) 的作者說明:

> 課程網站若抓取過快很容易被封鎖,因此本爬蟲有限制同一時間抓取頁面數量。
> 我抓二十年的資料花了大概兩天。

**因此本專案硬性規定**:

- **單執行緒**,不使用 `threading` / `asyncio` / `multiprocessing` 平行抓取
- 每次請求後強制 sleep(預設 1.0 秒,可由參數調整,但**下限 0.5 秒**)
- User-Agent 必須有辨識度並附聯絡方式
- 這些規則**不得為了「加快速度」而放寬**

> Phase 0 全程共只發出 6 次請求,間隔 2 秒。後續開發一律對 fixtures 進行,不再打學校伺服器。

**請求量估算(給 Phase 4 排程參考)**:單學期約 1 個總覽頁 + 約 60 個單位頁 + 每單位數個班級頁,粗估 300～500 次請求,以 1 秒間隔約 10 分鐘內可完成。教學大綱則是每門課一次(數千次),另計,見 Phase 6。

### 1.5 SSL 憑證問題 ✅

Python 內建 `ssl`(OpenSSL 3.x 嚴格模式)會拒絕學校憑證:

```
CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier
```

但 `curl`(走 Windows schannel)與瀏覽器都能正常驗證。判定是憑證缺少非必要的擴充欄位,被 OpenSSL 嚴格模式擋下,**不是真的中間人風險**。

**解法**:使用 [`truststore`](https://pypi.org/project/truststore/) 讓 Python 走系統憑證庫驗證,行為與 curl / 瀏覽器一致:

```python
import truststore
truststore.inject_into_ssl()   # 必須在 import requests 之前
import requests
```

**不要用 `verify=False` 關閉驗證**,那是把問題蓋掉而不是解掉。

❓ 本機環境是 Python 3.14(OpenSSL 嚴格模式);CI 指定 Python 3.12,可能根本不會遇到這個錯誤。**Phase 5 第一次跑 workflow 時要確認 Linux 上的行為**,若 `truststore` 在 Linux 反而出問題,就改成只在需要時注入。

---

## 2. 專案結構

```
ntut-course-crawler/
├─ .github/
│  └─ workflows/
│     └─ crawl.yml
├─ crawler/
│  ├─ __init__.py
│  ├─ http.py            # 唯一對外出口:session / 編碼 / retry / 限速 / 快取
│  ├─ models.py          # dataclass 定義輸出 schema
│  ├─ parse_util.py      # 解析器共用工具
│  ├─ parse_semester.py  # 解析 course.jsp — 目前有哪些學年期(Phase 7)
│  ├─ parse_dept.py      # 解析總覽頁與單位頁
│  ├─ parse_course.py    # 解析課程列表
│  ├─ parse_syllabus.py  # 解析教學大綱(Phase 6,未實作)
│  ├─ periods.py         # 上課時間代碼 → 結構化
│  ├─ output.py          # 各維度 JSON 輸出(Phase 7)
│  └─ main.py            # CLI entry point
├─ tests/
│  ├─ fixtures/          # 真實 HTML 樣本(Phase 0 已產出)
│  ├─ test_http.py
│  ├─ test_parse_semester.py
│  ├─ test_parse_dept.py
│  ├─ test_parse_course.py
│  ├─ test_output.py
│  ├─ test_main.py
│  └─ test_periods.py
├─ scripts/
│  ├─ recon.py           # Phase 0 偵察腳本
│  ├─ recon2.py          # Phase 0 第二輪(真實系所)
│  ├─ recon3.py          # Phase 3 進修部確認
│  └─ recon4.py          # Phase 8 歷史學年期可用性
├─ .gitignore
├─ requirements.txt
└─ README.md
```

### 依賴 (`requirements.txt`)

```
requests>=2.32
beautifulsoup4>=4.12
lxml>=5.0
tenacity>=9.0
truststore>=0.9
```

**不要引入 Scrapy** — 站台階層很淺,框架成本大於收益。

### `.gitignore`

```
__pycache__/
*.pyc
.cache/
data/
.venv/
```

---

## 3. 開發階段

### ✅ Phase 0 — 偵察(已完成 2026-09-03)

**產出的 fixtures**(全部在 `tests/fixtures/`):

| 檔案 | 內容 |
|---|---|
| `subj_overview.html` | `format=-2` 學院 / 單位總覽 |
| `dept_page.html` | `format=-3` 教務處(行政單位範例) |
| `dept_page_real.html` | `format=-3` 資工系(真實系所範例) |
| `course_list_real.html` | `format=-4` 資工四課程列表 |
| `syllabus_page_real.html` | `ShowSyllabus.jsp` 教學大綱頁 |

**結論**:編碼 UTF-8、階層多一層、SSL 需 `truststore`,詳見 §1.1 / §1.3 / §1.5。

**之後所有解析器開發都對著 fixtures 進行,不再重複打學校伺服器。**

---

### Phase 1 — `http.py`(全專案風險最高的一塊)

**設計原則**:整個專案**只有這個模組**能發出對外請求。

**必須實作**:

```python
def fetch(url: str, *, params: dict | None = None) -> str:
    """回傳已正確解碼的 HTML 字串。"""
```

內部行為:

| 項目 | 要求 |
|---|---|
| SSL | `truststore.inject_into_ssl()`,且必須在 `import requests` 之前執行 |
| Session | 使用單一 `requests.Session()`,保留 cookie(站台會發 `JSESSIONID`) ✅ |
| User-Agent | `ntut-course-crawler/1.0 (+https://github.com/{USER}/{REPO})` |
| 編碼 | 固定 `content.decode('utf-8')` ✅,不要相信 `r.encoding` 自動判斷 |
| 限速 | 每次請求**後** sleep,預設 1.0s,可由環境變數 `CRAWL_DELAY` 覆寫,下限 0.5s |
| Timeout | `timeout=(10, 30)` (connect, read) |
| Retry | `tenacity`:5xx / Timeout / ConnectionError 時指數退避,最多 4 次,起始 2s |
| 不重試 | 4xx 直接拋出(重試沒意義,且可能是被擋) |
| 快取 | 見下方 |

**本地 HTTP 快取(重要)**:

- 快取目錄 `.cache/`,檔名為 URL(含 query)的 `sha256` hex
- 命中快取時**不 sleep、不發請求**
- CLI 提供 `--no-cache` 強制略過
- 理由:開發期會反覆執行;教學大綱等歷史資料幾乎不變;失敗重跑只補缺漏

**驗收條件**:

- 對 fixture 對應的 URL 呼叫 `fetch()`,能取回無亂碼中文
- 連續兩次呼叫同一 URL,第二次明顯更快(快取生效)
- 單元測試驗證 sleep 下限不可被設成 0

---

### Phase 2 — 資料模型 `models.py`

用 `dataclass`,並提供 `to_dict()`。**這是對外 API 的契約,請慎重設計。**

欄位已依 `course_list_real.html` 的**實際 23 欄**修正:

```python
@dataclass
class TimeSlot:
    day: int              # 0=日, 1=一, ..., 6=六
    periods: list[str]    # ["3", "4"] 或 ["N"], 保留原始代碼字元

@dataclass
class Course:
    id: str                    # 課號, 例 "364893"
    name_zh: str
    name_en: str | None        # ❓ 課程列表頁沒有英文名, 可能要從教學大綱取
    stage: str | None          # 階段, 例 "1"
    credits: float | None
    hours: int | None
    required: bool | None         # 必=True / 選=False, 由符號判定(對照表見 Phase 3)
    requirement_type: str | None  # 完整類別, 例 "專業選修" / "部訂共同必修"
    teachers: list[str]
    teacher_codes: list[str]   # Teach.jsp 的 code, 組教學大綱 URL 要用
    classes: list[str]         # 開課班級, 來自表格上方 <th colspan=23>
    time_slots: list[TimeSlot]
    classrooms: list[str]
    quota: int | None          # 人數
    withdrawn: int | None      # 撤選人數
    language: str | None       # 授課語言, 空白代表中文
    syllabus_url: str | None
    notes: str | None          # 備註, 例 "資工四和資工所合開"
    audit: str | None          # 隨班附讀
    lab: str | None            # 實驗 / 實習
    programs: list[str]        # 跨領域學程 / 微學程, 以 <BR> 分隔多值

@dataclass
class Department:
    id: str                    # 總覽頁的 code, 例 "59"
    name: str                  # 例 "資工系"
    college: str | None        # 學院, 例 "電資學院"; 行政單位為 None
    url: str

@dataclass
class ClassGroup:              # 新增: format=-4 那一層
    id: str                    # 例 "2915"
    name: str                  # 例 "資工四"
    department_id: str         # 例 "59"
    url: str
```

> **模型變更說明**:原規格的 `Department.education_type`(學制:日間部/進修部)**移除**,因為 `format=-2` 頁面實際提供的分組是**學院**(機電/工程/管理/設計/人文與社會科學/電資/創新前瞻科技研究),沒有學制欄位。進修部課程是否另有入口尚未確認,列入 §7。

**教學大綱 URL 可直接組出**(不需額外請求探索) ✅:

```
ShowSyllabus.jsp?snum={課號}&code={該課第一位教師的 teacher_code}
```

已驗證:`數位影像處理` 課號 364893 + 白敦文 code 12095 → `snum=364893&code=12095`,三筆樣本皆吻合。

**時間欄位務必結構化**。`periods.py` 負責把表格的 7 個星期欄位轉為 `TimeSlot`,並附完整單元測試涵蓋 `N`、`A`~`D` 等特殊節次。

節次對照(頁面底部即附此表)✅:

```
1: 08:10-09:00   2: 09:10-10:00   3: 10:10-11:00   4: 11:10-12:00   N: 12:10-13:00
5: 13:10-14:00   6: 14:10-15:00   7: 15:10-16:00   8: 16:10-17:00   9: 17:10-18:00
A: 18:30-19:20   B: 19:20-20:10   C: 20:20-21:10   D: 21:10-22:00
```

建議 `periods.py` 也輸出這張對照表(供下游渲染課表用),放進 `meta.json`。

---

### Phase 3 — 解析器

三個模組,**全部寫成純函式**:吃 HTML 字串 → 吐 dataclass。不在解析器內發網路請求。

```python
def parse_colleges(html: str) -> list[Department]: ...       # format=-2
def parse_class_groups(html: str) -> list[ClassGroup]: ...   # format=-3
def parse_courses(html: str) -> list[Course]: ...            # format=-4
```

**對 fixtures 開發,完全離線。**

每個解析器都要有對應測試,對 fixture 斷言關鍵欄位。學校改版時測試會先失敗,這是刻意的預警機制。建議斷言:

- `subj_overview.html` → 能取到「資工系」且其 `college == "電資學院"`
- `dept_page_real.html` → 5 個班級,含 `資工四` / code `2915`
- `course_list_real.html` → 6 門課,`364893` 的名稱是「數位影像處理」、學分 3.0、教師「白敦文」、時間為週五 2/3/4 節、教室「六教727(e)」

**實測的 HTML 特性(照這個寫,不要憑印象)** ✅:

- **`<tr>` / `<td>` 都沒有收尾標籤**,是老式 JSP 輸出。必須用 `BeautifulSoup(html, 'lxml')`,html.parser 容易切錯。
- **總覽頁用 `rowspan` 表示學院**:`<tr><td rowspan=3>機電學院` 後面接三個 `<tr>`,學院名稱只出現一次。parse 時要往回追 rowspan 才能把系所對到學院;第一列行政單位的學院欄是空白(`　`),對應 `college = None`。
- **課程表格固定 23 欄**,表頭為:課號、課程名稱、階段、學分、時數、修、教師、日、一、二、三、四、五、六、教室、人、撤、授課語言、教學大綱與進度表、備註、隨班附讀、實驗實習、跨領域。
- **班級名稱在表格第一列**:`<tr><th colspan=23>資工四`。
- **要跳過的列**:
  - 開頭的「班週會及導師時間」(課號欄空白,不是真的課)
  - 結尾的「小計」列(`<td align=CENTER>小計`,只有學分/時數合計)
  - 判斷方式:**課號欄為空或非數字就跳過**,不要用列的位置判斷
- **多值欄位以 `<BR>` 分隔**(教師、教室、跨領域學程),要拆成 list;不要用空白切,課程名稱本身可能含空白。
- **空欄位是全形空白 `　`(U+3000)**,不是空字串。正規化時要一併 strip 掉,否則會得到看起來是空、實際長度為 1 的字串。
- 教師與教室都包在 `<a>` 裡,`href` 帶 code(`Teach.jsp?...code=12095` / `Croom.jsp?...code=452`),要一併取出。

**必選修符號對照** ✅(來源:`Cprog.jsp?format=-5` 課程標準頁):

| 符號 | 必選修別 | 詳細類別 | `required` | `requirement_type` |
|---|---|---|---|---|
| ○ | 必 | 部訂共同必修 | `True` | `"部訂共同必修"` |
| △ | 必 | 校訂共同必修 | `True` | `"校訂共同必修"` |
| ☆ | **選** | 共同選修 | `False` | `"共同選修"` |
| ● | 必 | 部訂專業必修 | `True` | `"部訂專業必修"` |
| ▲ | 必 | 校訂專業必修 | `True` | `"校訂專業必修"` |
| ★ | **選** | 專業選修 | `False` | `"專業選修"` |

實作注意:

- 符號包在 `<A href="Cprog.jsp?format=-5">★</A>` 裡,取 anchor 的文字,不是 `href`
- **`★` 與 `☆` 都是「選」**。兩者差別在共同 / 專業,不是必 / 選
- 欄位為空(全形空白)時 `required = None`,**不要預設為 `False`**
- 遇到表上沒有的符號 → `required = None` + warning,不要猜

> 這解釋了 fixture 的現象:`course_list_real.html`(資工四)全部是 ☆ 與 ★,因為該頁列的本來就都是選修課。先前「★ 可能是必修」的推測是錯的,已由課程標準頁推翻。

**解析注意事項**:

- 欄位缺失時填 `None`,**不要拋例外中斷整批**;記錄 warning 繼續
- 遇到欄數不是 23 的列,記 warning 後跳過該列,不要讓整頁失敗

---

### Phase 4 — `main.py` 與輸出

**抓取流程**:

```
1. fetch format=-2         → parse_colleges  → list[Department]
2. for each Department:
     fetch format=-3       → parse_class_groups → list[ClassGroup]
3. for each ClassGroup:
     fetch format=-4       → parse_courses      → list[Course]
4. 依課號去重合併(同課號出現在多班級時, 合併 classes 欄位)
5. 寫出 JSON
```

**CLI**:

```bash
python -m crawler.main --year 115 --sem 1 --out data/
python -m crawler.main --year 115 --sem 1 --out data/ --no-cache --delay 1.5
python -m crawler.main --year 115 --sem 1 --out data/ --dept 59   # 只抓單一系所, 開發用
```

> `--dept` 改用**系所代碼**(如 `59`)而非中文名,因為系所短名(資工系)與全名(資訊工程系)不一致,用代碼較穩定。

**輸出結構**:

```
data/
├─ meta.json                      # 產生時間、schema_version、涵蓋的學年期、節次對照表、必選修符號對照
├─ index.json                     # 輕量索引,供前端載入後自行 filter
└─ 115-1/
   ├─ departments.json            # 含學院、系所、班級三層對照
   └─ courses/
      ├─ 59.json                  # 以系所代碼命名
      └─ 31.json
```

> **檔名變更說明**:原規格用中文檔名(`資訊工程系.json`)。改用**系所代碼**,理由:中文檔名在 GitHub Pages 上會被 percent-encoding,使用者要自己處理 URL 編碼;且系所可能改名,代碼較穩定。中文名稱在 `departments.json` 裡提供對照。

**`index.json` 設計**:只含 `id` / `name_zh` / `teachers` / `time_slots` / `dept` / `credits`。目的是讓前端一次載入就能做關鍵字搜尋,不必逐系所請求。控制在合理大小(單學期應在數百 KB 以內)。

**每個 JSON 檔頂層必須含 `"schema_version": 1`。** API 一旦有人使用,格式變更就是 breaking change,需要版本標記。

**錯誤處理**:

- 單一系所抓取失敗 → 記錄到 `data/errors.json`,繼續下一個
- 全部失敗才以非零 exit code 結束
- 結束時印出摘要:成功/失敗系所數、課程總數、耗時

---

### Phase 5 — GitHub Actions

`.github/workflows/crawl.yml`:

```yaml
name: crawl

on:
  schedule:
    - cron: '0 18 * * *'    # UTC 18:00 ≈ 台灣時間 02:00
  workflow_dispatch:         # 手動觸發(務必保留)
    inputs:
      year:
        default: '115'
      sem:
        default: '1'

permissions:
  contents: write

jobs:
  crawl:
    runs-on: ubuntu-latest
    timeout-minutes: 330      # Actions 單 job 上限 6 小時,留緩衝
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Crawl
        run: |
          python -m crawler.main \
            --year "${{ inputs.year || '115' }}" \
            --sem  "${{ inputs.sem  || '1' }}" \
            --out data/

      - name: Publish to gh-pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./data
          force_orphan: true    # 不保留歷史,避免 repo 無限膨脹
```

**注意事項**:

- **第一次跑要先確認 SSL 在 Linux 能通**(見 §1.5),建議先用 `workflow_dispatch` 手動跑一次只抓單一系所
- `workflow_dispatch` 必須保留,否則每次測試都要等 cron
- GitHub Actions 的 cron 在尖峰時段常延遲十幾分鐘,對本專案無影響
- 若要抓歷年資料,**不要在單一 job 跑完**,改用 matrix 每次一個學年,或分多次手動觸發
- `force_orphan: true` 讓 gh-pages 每次覆蓋,repo 不會因每日 commit 完整 JSON 而膨脹

---

### Phase 6 — 教學大綱(可選,量大)

只在前面都穩定後才做。教學大綱是 N+1 請求(每門課一次),以 1 秒間隔計算,單學期數千門課即需數小時。

建議:

- 獨立 CLI flag `--with-syllabus`
- 預設關閉
- 高度依賴 `.cache/`,歷史學期抓過一次就不用再抓
- 輸出到獨立檔案 `data/115-1/syllabus/{course_id}.json`,不塞進主課程檔
- URL 可由課號 + 教師代碼直接組出(見 Phase 2),不需先探索連結

---

### Phase 7 — 分類索引與學年期自動偵測(已完成 2026-09-04)

Phase 4 的輸出把整個學期壓在少數幾個檔案裡。要查某個系所或某位老師的課,
得先把全校兩千多門課整包下載下來再自己過濾 —— 對前端來說太重了。
這個階段把資料依維度拆開,順便把排程改成 4 小時一次,
並讓學年期不再寫死在程式或 workflow 裡(115-2、116-1 之後都不用改碼)。

#### 7.1 分類輸出(`crawler/output.py`)

把 `main.py` 裡的輸出邏輯整支搬到 `output.py`,並補上各維度的索引:

| 維度 | 清單檔 | 明細檔 | 說明 |
|---|---|---|---|
| 系所 | `departments.json` | `courses/{department_id}.json` | 既有 |
| 教師 | `teachers.json` | `teachers/{teacher_id}.json` | 新增,803 檔 |
| 班級 | `classes.json` | `classes/{class_id}.json` | 新增,286 檔 |
| 學程 | `programs.json` | —— | 學程只有中文名,沒有能當檔名的代碼 |
| 教室 | `classrooms.json` | —— | 234 間 |
| 時段 | `schedule.json` | —— | 星期 × 節次 → 課號 |
| 全部 | `index.json` / `{semester}/index.json` | —— | 輕量索引 |

設計取捨:

- **明細檔放完整課程物件,清單檔只放「有哪些 + 幾門課」。** 前端做下拉選單時
  不必先吞下整包資料;點下去才載明細。
- **教師一律以代碼為 key,不是姓名。** 115-1 實測 803 個代碼只對到 801 個姓名,
  確實有同名老師。
- `programs` / `classrooms` / `schedule` 只放課號,不複製課程物件 ——
  否則同一門課會被抄進四五個檔案,總體積失控。
- **代碼要驗證才能當檔名。** 代碼是從 HTML 抓來的字串,直接拿去組路徑等於
  把 path traversal 的權力交給對方頁面。`_safe_id()` 只放行 `[A-Za-z0-9_-]{1,64}`。
- **完整抓取前先清掉 `courses/` `teachers/` `classes/`。** 班級改號、系所裁撤時,
  舊檔留在原地會變成永遠不會更新、卻查得到的幽靈資料。`--dept` 局部抓取
  不清(會誤刪沒抓的系所),並在 `meta.json` 標 `partial: true`。

115-1 實測:1159 個檔、6.5 MB;`index.json` 711 KB(gzip 61 KB)。

#### 7.2 學年期自動偵測(`crawler/parse_semester.py`)

學年期不再寫死。`course.jsp` 首頁本來就會列出所有「上課時間表」入口,
一個學期一個 `Subj.jsp?format=-2&year=&sem=` 連結,抓這些連結就等於
問學校「你現在有哪幾個學期」。

`select_semesters()` 的取捨規則:

- **最新學期每次都抓** —— 選課期間資料每天在動。
- 沒有資料的學期抓 —— 新學期一掛出來就補上。
- 其他學期超過 `--refresh-after`(預設 24 小時)才重抓。
  排程是 4 小時一次,若每次都把所有學期重掃一遍,等於對學校的請求量乘以學期數,
  而過去的學期資料幾乎不再變動,不值得。
- 首頁已下架、本地還有資料的學期不抓也不刪 —— 歷史資料留著。

`--year` / `--sem` 改成選填(要嘛都給,要嘛都不給)。都不給就走自動偵測。

#### 7.3 workflow

- cron 由 `0 18 * * *` 改成 `0 */4 * * *`。
- 新增 **Restore previously published data** 步驟:發布用的是 `force_orphan`
  (不留歷史),`data/` 每次都是空的。不先把上次的成果撈回來的話,
  `meta.json` / `index.json` 沒有舊學期可合併,歷史學期會整個消失,
  而且判斷不出「這學期上次何時抓的」,`--refresh-after` 形同虛設。
- `workflow_dispatch` 的參數改走 `env:`,不直接內插進 shell(script injection)。


---

### Phase 8 — 歷史學期回補與 schema v2(已完成 2026-09-04)

課程系統首頁只掛最近兩個學期,但「首頁沒列出」不等於「資料已經下架」。
這個階段先實測舊學年期是否仍可直接存取,再決定要不要回補。

#### 8.1 偵察結果(`scripts/recon4.py`,13 次請求)

首頁只掛最近兩個學期,但 `Subj.jsp` 是老式 JSP,直接吃 query string 查資料庫,
**完全不理會首頁有沒有放連結**:

| 學年期 | 總覽頁單位數 | | 學年期 | 總覽頁單位數 |
|---|---|---|---|---|
| 114-1 | 61 | | 105-1 | 53 |
| 113-1 | 60 | | 95-1 | 47 |
| 113-2 | 60 | | 90-1 | 45 |
| 110-1 | 60 | | 85-1 | 6(殘缺) |
| 95-2 | 47 | | 80-1 | 0(空頁) |

**完整資料到 90 學年度為止。** 而且整棵樹都活著 —— 往下鑽 95-1 資工系,
`format=-3` 回 4 個班級、`format=-4` 回 12 門課,**零 warning**。
表頭與 115-1 完全相同(23 欄,含授課語言與跨領域),教師與教室一樣是帶 code
的 `Teach.jsp` / `Croom.jsp` 連結。結論:**現有解析器不用改任何一行**。

#### 8.2 為什麼必須先升 schema v2

原本的設計撐不住 25 年的資料量,兩個地方會爆:

1. **頂層 `index.json` 含全部學期** → 50 個學期會膨脹到數十 MB,
   而且它每次抓取都重寫。改成只涵蓋最新 `INDEX_SEMESTERS`(2)個學期,
   並新增 `covers` 欄位明講範圍;歷史學期查 `{semester}/index.json`。
2. **每個檔都有 `generated_at`** → 每次跑完所有檔案內容都變,
   發布時等於整包重推。改成只留在 `meta.json` / `errors.json`。
   時間戳集中在 meta 粒度也更正確:一個學期的檔案本來就是同時產生的。

第 2 點的效果:同樣的抓取結果重跑一次,學期檔案 **byte 級相同**
(`test_rerunning_produces_byte_identical_semester_files`),
所以沒有真的變動時不會產生假 diff。

兩項都是**改既有語意**,依 README 的相容性承諾升版到 2。專案上線兩天、
幾乎沒有外部使用者,現在付這個代價最便宜。

#### 8.3 發布策略:keep_files 取代 force_orphan

`force_orphan` 每次把整個 `data/` 重推。archive 到數百 MB 時,
等於每天推 6 次數百 MB —— 不是慢一點的問題,是在濫用免費資源。

改成:

- 例行抓取用 `keep_files: true`,`publish_dir` 只有當期的資料,
  歷史學期原封不動留在分支上
- 抓取前只從 gh-pages 撈 `meta.json` / `index.json` / `errors.json` 三個檔
  (`git clone --filter=blob:none --sparse`,只 checkout 根目錄的檔案,
  2 MB 而不是數百 MB)。理由見 `.github/workflows/restore-note.md`
- 代價是班級 / 教師消失時遠端會留下幽靈檔,由 `crawl.yml` 的 `rebuild`
  參數整包重發清掉,順便把 gh-pages 的 commit 歷史壓成一個

**踩過的坑**:`--filter=blob:none --no-checkout` 之後 `git checkout HEAD -- <path>`
會失敗(`unable to read sha1 file`),lazy fetch 在這條路徑上不會觸發。
改用 `--sparse` —— 它剛好只 checkout 根目錄下的檔案,正是需要的那三個。

#### 8.4 回補 CLI

- `--years 90-114`:抓範圍內**尚未抓過**的學期,由新到舊
- `--max-semesters N`:分批,避開 Actions 單 job 6 小時上限

**抓過就永久跳過,不看新舊**(有別於 `--refresh-after` 對當期學期的處理)。
過去的學期不會再變動,重抓沒有意義;更重要的是這讓回補**可以續跑** ——
跑到一半失敗、或分批跑,再執行一次就會自動接上,不必記到哪了。

太舊而總覽頁是空的學年期(80-1 以前)不會寫出空目錄,也不會在 meta.json
留下「抓過了」的紀錄擋住之後的重試。

#### 8.5 學期層級的容錯(回補第一批失敗後補上)

第一批回補在 114-1 的總覽頁 `ConnectTimeout` 掛掉(台灣時間 02:01,
疑似學校深夜維護;十分鐘後從本機測同一個 URL 是通的,2.3 秒回應)。

真正的問題不是逾時,是**容錯少了一層**:`crawl()` 有系所層級與班級層級的
try/except,卻沒有學期層級的。總覽頁抓不到時例外一路往上炸掉整個執行,
workflow 的 Publish 步驟因而被跳過 —— 一批 12 個學期若跑到第 8 個才掛,
前 7 個抓好的資料會全部白做。

修法:`main()` 的學期迴圈包 try/except,失敗的學期記進 errors.json
(`stage: "semester"`)後換下一個;**刻意不寫進 meta.json**,
否則下次會誤以為抓過了而永遠不重試。只有「所有學期都失敗」才回傳 1。

`errors.json` 與 `meta.json` 的分工在這裡必須講清楚:
errors.json 是「這次發生什麼事」,meta.json 是「我手上有什麼資料」。

#### 8.6 連續失敗就收手(第二批失敗後補上)

補了 8.5 之後重跑,這次 **12 個學期全滅**。精確時間排出來才看懂:

| 時間(UTC) | 事件 | 結果 |
|---|---|---|
| 16:51–17:09 | crawl,705 次請求 | 成功 |
| 18:00–18:01 | backfill #1 | 失敗 |
| 18:06–18:18 | backfill #2,12 個學期全掛 | 失敗 |
| 18:19–18:20 | crawl 探測(8 次請求) | **成功** |

18:00–18:18 這 18 分鐘 GitHub runner 完全連不到學校,18:19 就恢復;
同一時間本機一直是通的(2.1 秒回應)。TCP connect timeout 與 query string
無關,所以「舊學年被擋」不成立 —— 是 Azure 到學校那段的網路狀況,
或短暫封鎖後自動解除。**不是我們被永久封鎖**。

但 backfill #2 花掉的 11.5 分鐘正好是「12 個學期 × 4 次重試 ×(10 秒逾時
+ backoff)」—— 也就是中斷期間我們還在對一台連不上的機器持續重試 11 分鐘。
單一學期失敗可能只是那頁有問題,但**連續失敗幾乎一定是對方整體不可用**,
繼續試沒有意義也不禮貌。

修法:連續 `CONSECUTIVE_FAILURE_LIMIT`(3)個學期整個抓不到就中止本批。
中間夾著任何一次成功就把計數器歸零(散落的失敗不該被誤判成對方掛了)。
中止不影響已抓好的學期 —— 它們每抓完一個就落地了。

---

## 4. README 需包含的內容

給資料使用者看的:

- API base URL(GitHub Pages 網址)
- 各端點路徑與範例回應
- `schema_version` 說明與相容性承諾
- 資料更新頻率
- 資料來源聲明(北科大課程系統)與免責聲明:非官方、僅供參考、以學校公告為準
- 授權(建議 MIT,資料本身標註來源)

---

## 5. 開發原則(執行守則)

1. **階段順序不可跳過**。Phase 0 已完成,fixtures 已在 `tests/fixtures/`。
2. **不要為了加速而移除限速或改用平行抓取**,即使測試時覺得慢。
3. **開發期一律使用快取或 fixtures**,不要為了驗證而反覆打學校伺服器。若真的需要新樣本,一次只抓一頁並說明理由。
4. **不要猜測 HTML 結構**。§3 Phase 3 列的特性都是實測結果;若與現況不符,以 fixture 為準並回報差異。
5. 解析器遇到非預期結構時,**降級處理(填 None + warning)而非拋例外**。
6. 每個 Phase 完成後先確認驗收條件全數通過,再進入下一個。
7. 遇到 ❓ 標記的項目,**先確認再實作**,不要當成已知條件往下寫。

---

## 6. 未來擴充方向(本次不做)

- Cloudflare Worker 讀取 gh-pages JSON,提供伺服器端查詢 API
- 前端網站(衝堂偵測、課表模擬)
- 教學大綱內容(Phase 6)

---

## 7. ❓ 待確認事項(實作到對應階段時處理)

目前沒有待確認事項。

### 已解決

- ~~學年度 / 學期的有效範圍,以及舊學年頁面結構是否相同~~ → 2026-09-04 Phase 7 處理掉了。
  「有效範圍」不需要猜:`course.jsp` 首頁會列出目前開放的每個學期,程式讀它就好
  (`crawler/parse_semester.py`)。舊學年頁面結構相同 —— 以 114-2 資工系實測,
  同一套解析器直接吃下去,57 門課全部解析正常,不需分支處理。

- ~~`truststore` 在 GitHub Actions(Linux + Python 3.12)是否需要 / 是否反而出錯~~ → 2026-09-03 第一次 `workflow_dispatch` 冒煙測試(run 33756114153,`dept=59`)確認:Linux + Python 3.12.14 上 `truststore` 正常注入,7 次請求全部成功,log 沒有任何 SSL 或注入失敗的 warning,結果與本機完全一致(53 門課)。無需特別處理。

- ~~進修部 / 研究所在職專班課程是否在同一個 `format=-2` 樹裡~~ → 2026-09-03 以兩次實地請求確認(`scripts/recon3.py`):
  1. 系統首頁 `course.jsp` 每個學期**只提供一個「上課時間表」入口**,就是 `Subj.jsp?format=-2`,沒有另一個進修部入口。
  2. 抽查機械系(`code=30`)的 `format=-3`,只有機械一~四甲乙共 8 個班,**沒有任何夜間班**;資工系亦同。
  結論:`format=-2` 這棵樹就是這個系統公布的全部,爬完即無遺漏。若學校另有進修部課表,不在 `aps.ntut.edu.tw/course/tw/` 底下,屬本專案範圍外,已寫入 README 的資料範圍聲明。
- ~~課程英文名稱來源~~ → 2026-09-03 確認 `course_list_real.html` 與 `syllabus_page_real.html` **都沒有英文課名**。`Course.name_en` 保留在 schema 中(維持契約穩定)但恆為 `null`。系統首頁另有「英語授課課程查詢專區」(`ShowENSubject.jsp`),不在 MVP 範圍。

- ~~`★` / `☆` 的確切語意~~ → 2026-09-03 依學校公布的課程標準頁確認,對照表已寫入 Phase 3。原推測(★=必修)為誤,實際上 ★ 與 ☆ 都是**選修**。
