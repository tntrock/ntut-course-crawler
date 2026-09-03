# ntut-course-crawler

國立臺北科技大學課程資料的**非官方**靜態 API。

每天自動爬取學校課程查詢系統，轉成結構化 JSON 後發布到 GitHub Pages。
沒有伺服器、沒有資料庫、不需要 API key —— 直接 `fetch()` 就能用。

---

## API base URL

```
https://tntrock.github.io/ntut-course-crawler/
```

> 下面所有路徑都是相對於這個 base URL。

## 端點

| 路徑 | 內容 | 大小 |
|---|---|---|
| `meta.json` | 產生時間、涵蓋的學年期、節次對照表、必選修符號對照 | 小 |
| `index.json` | 全部課程的輕量索引，適合一次載入後在前端做關鍵字搜尋 | 數百 KB |
| `{year}-{sem}/departments.json` | 學院 / 系所 / 班級三層對照 | 小 |
| `{year}-{sem}/courses/{系所代碼}.json` | 該系所的完整課程資料 | 小 |
| `errors.json` | 最近一次爬取失敗的單位（正常情況是空陣列） | 小 |

`{year}-{sem}` 例如 `115-1`；`{系所代碼}` 例如 `59`（資工系）。

### 為什麼檔名是代碼而不是中文？

中文檔名在 GitHub Pages 上會被 percent-encoding，使用者得自己處理 URL 編碼；
而且系所會改名，代碼相對穩定。中文名稱在 `departments.json` 裡提供對照。

---

## 範例

### `meta.json`

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-03T18:03:11Z",
  "source": {
    "name": "國立臺北科技大學 課程查詢系統",
    "url": "https://aps.ntut.edu.tw/course/tw/"
  },
  "disclaimer": "本資料由非官方爬蟲自動蒐集,僅供參考,一切以學校公告與課程系統當下顯示的內容為準。",
  "semesters": [
    {
      "year": 115, "sem": 1, "path": "115-1",
      "generated_at": "2026-09-03T18:03:11Z",
      "department_count": 60,
      "class_group_count": 412,
      "course_count": 5123,
      "merged_course_count": 0,
      "failed_department_count": 0
    }
  ],
  "periods": [
    { "code": "1", "start": "08:10", "end": "09:00" },
    { "code": "N", "start": "12:10", "end": "13:00" },
    { "code": "A", "start": "18:30", "end": "19:20" }
  ],
  "requirement_symbols": [
    { "symbol": "★", "required": false, "requirement_type": "專業選修" }
  ]
}
```

### `115-1/departments.json`

```json
{
  "schema_version": 1,
  "year": 115,
  "sem": 1,
  "generated_at": "2026-09-03T18:03:11Z",
  "departments": [
    {
      "id": "59",
      "name": "資工系",
      "college": "電資學院",
      "url": "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-3&year=115&sem=1&code=59",
      "class_groups": [
        {
          "id": "2915",
          "name": "資工四",
          "url": "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-4&year=115&sem=1&code=2915"
        }
      ],
      "course_count": 53
    }
  ]
}
```

行政單位（教務處、體育室、通識中心…）的 `college` 是 `null`。

### `115-1/courses/59.json`

```json
{
  "schema_version": 1,
  "year": 115,
  "sem": 1,
  "generated_at": "2026-09-03T18:03:11Z",
  "department": { "id": "59", "name": "資工系", "college": "電資學院", "url": "..." },
  "courses": [
    {
      "id": "364893",
      "name_zh": "數位影像處理",
      "name_en": null,
      "stage": "1",
      "credits": 3.0,
      "hours": 3,
      "required": false,
      "requirement_type": "專業選修",
      "teachers": ["白敦文"],
      "teacher_codes": ["12095"],
      "classes": ["資工四"],
      "class_ids": ["2915"],
      "department_ids": ["59"],
      "time_slots": [
        { "day": 5, "day_name": "五", "periods": ["2", "3", "4"] }
      ],
      "classrooms": ["六教727(e)"],
      "classroom_codes": ["452"],
      "quota": 22,
      "withdrawn": 0,
      "language": null,
      "syllabus_url": "https://aps.ntut.edu.tw/course/tw/ShowSyllabus.jsp?snum=364893&code=12095",
      "notes": "資工四和資工所合開",
      "audit": null,
      "lab": null,
      "programs": ["人工智慧科技學程", "光電智慧製造學程"]
    }
  ]
}
```

### `index.json`

只放搜尋需要的欄位，細節請到各系所檔案取。

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-03T18:03:11Z",
  "course_count": 5123,
  "courses": [
    {
      "id": "364893",
      "name_zh": "數位影像處理",
      "teachers": ["白敦文"],
      "time_slots": [{ "day": 5, "day_name": "五", "periods": ["2", "3", "4"] }],
      "department_ids": ["59"],
      "credits": 3.0,
      "year": 115,
      "sem": 1
    }
  ]
}
```

---

## 欄位說明

### 時間

`time_slots` 每個元素代表**一天**。`day` 是 0=日、1=一 … 6=六，
`periods` 是節次代碼，對應時間查 `meta.json` 的 `periods`。
沒有上課時間的課（例如體育、班週會）`time_slots` 是空陣列。

### 必選修

`required` 是布林值，`requirement_type` 是完整類別：

| 符號 | `required` | `requirement_type` |
|---|---|---|
| ○ | `true` | 部訂共同必修 |
| △ | `true` | 校訂共同必修 |
| ☆ | `false` | 共同選修 |
| ● | `true` | 部訂專業必修 |
| ▲ | `true` | 校訂專業必修 |
| ★ | `false` | 專業選修 |

**注意 ★ 和 ☆ 都是「選修」**，兩者差別在共同 / 專業，不是必 / 選。
原始資料該欄空白時 `required` 是 `null`，不是 `false`。

### 空值

原始頁面用全形空白 `　`(U+3000)表示「沒有這個欄位」，
輸出時一律正規化成 `null`（陣列欄位則是 `[]`）。

---

## `schema_version` 與相容性承諾

每個 JSON 檔的頂層都有 `"schema_version"`。目前是 **1**。

- **新增欄位不會升版。** 請用「忽略不認得的欄位」的方式寫你的程式。
- **移除欄位、改欄位型別、改欄位語意會升版**，並在此處與 Release Notes 說明。
- 升版時舊版路徑不保證保留 —— 這是免費的靜態檔案服務，請鎖定你測試過的版本號，
  發現 `schema_version` 變了就先檢查再上線。

## 更新頻率

每天 UTC 18:00（台灣時間隔天 02:00）自動跑一次。
GitHub Actions 的排程在尖峰時段常延遲十幾分鐘，屬正常現象。

`meta.json` 的 `generated_at` 是該次產生的實際時間，請以它為準。

---

## 資料範圍與已知限制

- **只涵蓋課程查詢系統的「上課時間表」**（`Subj.jsp?format=-2` 那棵樹）。
  該系統每個學期只提供這一個上課時間表入口，因此這棵樹爬完即無遺漏。
  暑期課程（`Summer.jsp`）、學程查詢、教室使用情形不在本專案範圍。
- **沒有進修部的獨立入口。** 實地確認過總覽頁的 60 個單位裡沒有進修部，
  抽查機械系、資工系的班級列表也沒有夜間班。若學校另有進修部課表，
  不在 `aps.ntut.edu.tw/course/tw/` 底下。
- **`name_en` 永遠是 `null`。** 課程列表頁與教學大綱頁都沒有英文課名，
  目前無資料來源。欄位保留是為了維持 schema 穩定。
- **合開課程在各班級是不同課號。** 例如「數位影像處理」在資工四是 `364893`、
  在資工所是 `364899`，`notes` 都寫「資工四和資工所合開」。
  不要假設「同一門課 = 同一個課號」，要判斷是否同一門課請一併看 `notes`。
- **`quota` 是原始頁面的「人」欄位**，會隨選課進度變動，只反映抓取當下的狀態。
- 教學大綱內容（Phase 6）尚未實作，目前只提供 `syllabus_url` 連結。

---

## 免責聲明

本專案為**非官方**個人專案，與國立臺北科技大學無隸屬關係。

資料以自動化方式蒐集自[北科大課程查詢系統](https://aps.ntut.edu.tw/course/tw/course.jsp)，
可能因學校改版、網路異常或解析錯誤而不完整或過時。
**選課、抵免、畢業學分等一切事項請以學校公告與課程系統當下顯示的內容為準**，
因使用本資料造成的任何損失，本專案不負責任。

若校方認為本專案造成困擾，請開 issue 告知，我會立即停止。

---

## 授權

程式碼採 [MIT License](LICENSE)。

資料本身著作權屬國立臺北科技大學，本專案僅為格式轉換與再發布。
使用資料時請標註來源為「國立臺北科技大學課程查詢系統」。

---

## 開發

### 環境

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements-dev.txt
```

### 執行

```bash
python -m crawler.main --year 115 --sem 1 --out data/
python -m crawler.main --year 115 --sem 1 --out data/ --dept 59 --pretty  # 開發用
python -m crawler.main --year 115 --sem 1 --out data/ --no-cache --delay 1.5
```

| 參數 | 說明 |
|---|---|
| `--dept CODE` | 只抓指定系所（可重複）。輸出會是不完整的資料集 |
| `--delay` | 每次請求後的延遲秒數，**下限 0.5** |
| `--no-cache` | 略過 `.cache/`，強制重新抓取 |
| `--pretty` | 輸出縮排過的 JSON（檔案會變大） |

### 測試

```bash
pytest -q
```

測試**完全離線**，全部對 `tests/fixtures/` 裡的真實 HTML 樣本斷言。
`tests/conftest.py` 有一道安全網，任何測試只要發出真實 HTTP 請求就會直接失敗。

解析器測試刻意寫得很細 —— 學校改版時它們會先紅，
CI 就不會把解錯的資料發布出去。

### 抓取禮儀（請勿放寬）

- **單執行緒**，不使用 threading / asyncio / multiprocessing 平行抓取
- 每次請求後強制 sleep，預設 1.0 秒，**下限 0.5 秒**（`crawler/http.py` 硬性限制）
- workflow 設了 `concurrency` 群組，不會有兩個 job 同時抓
- User-Agent 帶專案網址，方便校方辨識與聯絡

課程網站抓太快很容易被封鎖。這些限制不是效能問題，是能不能長久運作的問題。

### 架構

```
crawler/
  config.py       # 常數(base URL / schema 版本 / 延遲 / User-Agent)
  http.py         # 全專案唯一對外出口:限速、快取、重試、UTF-8 解碼
  models.py       # 輸出 schema 的 dataclass 定義
  periods.py      # 節次代碼對照與解析
  parse_util.py   # 解析器共用工具
  parse_dept.py   # 解析 format=-2 / -3(純函式,不連網)
  parse_course.py # 解析 format=-4(純函式,不連網)
  main.py         # CLI:抓取流程、去重、寫出 JSON
```

解析器一律是**純函式**：吃 HTML 字串 → 吐 dataclass，不發網路請求。
所有網路存取都集中在 `http.py`，限速與快取的規則只要在那裡守住就守住了。

完整的開發規格與偵察紀錄見 [`plan.md`](plan.md)。
