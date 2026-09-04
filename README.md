# ntut-course-crawler

[![test](https://github.com/tntrock/ntut-course-crawler/actions/workflows/test.yml/badge.svg)](https://github.com/tntrock/ntut-course-crawler/actions/workflows/test.yml) [![crawl](https://github.com/tntrock/ntut-course-crawler/actions/workflows/crawl.yml/badge.svg)](https://github.com/tntrock/ntut-course-crawler/actions/workflows/crawl.yml)

國立臺北科技大學課程資料的**非官方**靜態 API。

每 4 小時自動爬取學校課程查詢系統，轉成結構化 JSON 後發布到 GitHub Pages。
沒有伺服器、沒有資料庫、不需要 API key —— 直接 `fetch()` 就能用。

資料依**系所 / 教師 / 班級 / 學程 / 教室 / 時段**分別建索引，
查一位老師的課不必先下載全校兩千多門課。

涵蓋 **90-1 至 115-1 共 51 個學期、140,756 門課**（民國 90 年起，約 25 年）。

---

## API base URL

```
https://tntrock.github.io/ntut-course-crawler/
```

> 下面所有路徑都是相對於這個 base URL。`{semester}` 例如 `115-1`。

所有回應都帶 `Access-Control-Allow-Origin: *`，**瀏覽器可以直接跨網域 `fetch()`**，
不需要 proxy。`Content-Type` 是 `application/json; charset=utf-8`，
`Cache-Control` 是 `max-age=600`（GitHub Pages 的設定，本專案改不了）。

## 我想查…

| 我想查 | 打這支 | 例 |
|---|---|---|
| 現在有哪幾個學期、有什麼端點 | `meta.json` | |
| 關鍵字搜尋全部課程 | `{semester}/index.json` | `115-1/index.json` |
| **某個系所**的課 | `{semester}/courses/{系所代碼}.json` | `115-1/courses/59.json` |
| **某個老師**的課 | `{semester}/teachers/{教師代碼}.json` | `115-1/teachers/12095.json` |
| **某個班級**的課表 | `{semester}/classes/{班級代碼}.json` | `115-1/classes/2915.json` |
| 有哪些老師 / 班級 / 系所 | `{semester}/teachers.json`、`classes.json`、`departments.json` | |
| **某個學程**有哪些課 | `{semester}/programs.json` | |
| **某間教室**排了什麼課 | `{semester}/classrooms.json` | |
| **星期幾第幾節**有什麼課（找空堂、擋修衝堂） | `{semester}/schedule.json` | |
| 最近有哪些課 / 老師被加開、停開、調課、換人 | `changes.json` | |

不知道代碼？先拉 `{semester}/departments.json`、`teachers.json`、`classes.json`，
每一筆都附了 `path` 欄位，直接接在 base URL 後面就是明細檔的網址。

## 端點總覽

| 路徑 | 內容 | 115-1 實際大小 |
|---|---|---|
| `meta.json` | 學年期清單、端點清單、節次與必選修對照 | 13 KB |
| `index.json` | **最新兩個學期**的課程輕量索引 | 1.5 MB（gzip 149 KB）|
| `errors.json` | 各學期抓取失敗的單位（目前 51 個學期全部為空） | < 1 KB |
| `changes.json` | 最近的課程與教師異動事件（見下） | 隨異動量變動 |
| `{semester}/index.json` | **單一學期**的課程輕量索引 | 711 KB |
| `{semester}/departments.json` | 學院 / 系所 / 班級三層對照 | 48 KB |
| `{semester}/courses/{department_id}.json` | 系所課表（完整課程物件） | 60 檔，共 1.5 MB |
| `{semester}/teachers.json` | 教師清單 | 89 KB |
| `{semester}/teachers/{teacher_id}.json` | 教師課表（完整課程物件） | 803 檔，共 1.7 MB |
| `{semester}/classes.json` | 班級清單 | 70 KB |
| `{semester}/classes/{class_id}.json` | 班級課表（完整課程物件） | 286 檔，共 1.7 MB |
| `{semester}/programs.json` | 學程 → 課號 | 16 KB |
| `{semester}/classrooms.json` | 教室 → 課號 | 51 KB |
| `{semester}/schedule.json` | 星期 × 節次 → 課號 | 55 KB |

> 根目錄 `/` 是一頁靜態說明頁。要拿最新兩個學期的索引請明確打 `/index.json`。

**明細檔**（`courses/` `teachers/` `classes/`）放完整課程物件，拿到就能直接顯示，
不必再載別的檔對照。**清單檔**（`teachers.json` `classes.json` …）只放
「有哪些、各幾門課」，做下拉選單時不會被整包資料拖慢。

`programs.json` / `classrooms.json` / `schedule.json` 只放課號 —— 拿課號去
`{semester}/index.json` 或系所明細檔查即可，避免同一份課程資料被複製太多份。

### `changes.json`：最近的異動

排程每 4 小時重跑一次，但**大部分時候資料一個字都沒變**。光看檔案時間戳
分不出「只是重跑」和「學校真的動了課」，要確認就得自己下載前後兩版來 diff。

`changes.json` 是一條**事件流**：一筆異動一個事件、各自帶偵測到的時間，
最新的在最前面。想知道最近發生什麼，讀開頭幾筆就好。

```json
{
  "checked_at": "2026-09-04T12:03:11Z",
  "event_count": 4,
  "events": [
    {
      "at": "2026-09-04T12:03:11Z", "semester": "115-1",
      "type": "course_removed",
      "id": "361339", "name": "體育",
      "teachers": [], "department_ids": ["59"], "class_ids": ["2915"]
    },
    {
      "at": "2026-09-04T12:03:11Z", "semester": "115-1",
      "type": "course_changed",
      "id": "364893", "name": "數位影像處理",
      "teachers": ["陳香君"], "department_ids": ["59"], "class_ids": ["2915"],
      "changes": { "teachers": { "from": ["白敦文"], "to": ["陳香君"] } }
    },
    {
      "at": "2026-09-04T08:03:07Z", "semester": "115-1",
      "type": "teacher_removed",
      "id": "12095", "name": "白敦文",
      "course_count": 1, "department_ids": ["59"]
    }
  ]
}
```

| `type` | 意思 | 特有欄位 |
|---|---|---|
| `course_added` | 加開一門課 | |
| `course_removed` | 停開一門課 | |
| `course_changed` | 課的內容變了 | `changes`（逐欄位的 `from` / `to`） |
| `teacher_added` | 這學期多了一位老師 | `course_count`（他開幾門課） |
| `teacher_removed` | 這學期少了一位老師 | `course_count`（消失前開幾門課） |
| `baseline` | 第一次抓到這個學期，沒有基準可比 | `course_count` |
| `bulk_change` | 一次異動量異常，只記摘要 | `event_count`、`counts` |

- **`at` 是偵測到的時間，不是學校異動的時間** —— 實際異動發生在上一次抓取
  與這次之間，最多相差 4 小時。
- `checked_at` 是最後一次比對的時間，**沒有異動時也會更新**。所以
  「`checked_at` 是今天但沒有新事件」＝學校真的沒動；
  「`checked_at` 停在三天前」＝爬蟲沒在跑。
- `course_changed` 比對的欄位是課名、教師、時段、學分、必選修、開課系所、班級。
- **教師事件是用教師代碼比對的**，不是姓名 —— 115-1 實測 803 個代碼只對到
  801 個姓名，確實有同名老師。換人授課時課還在，只有教師端看得出少了誰。
- 最多保留 500 筆事件。
- **只有最新兩個學期會產生事件** —— 比對基準是頂層 `index.json`，它只涵蓋
  那兩個。第一次抓到某個學期時記成 `baseline`，不會變成「新增兩千多門課」。
- 單次超過 200 筆異動會折成一筆 `bulk_change`。學校端的正常異動是個位數，
  一口氣幾百筆通常是版面改了或抓歪了 —— 照實逐筆寫進去只會把先前真正的
  異動整個推出保留範圍，那是這個檔最不該失效的時候。

### 為什麼檔名是代碼而不是中文？

中文檔名在 GitHub Pages 上會被 percent-encoding，使用者得自己處理 URL 編碼；
而且系所會改名，代碼相對穩定。中文名稱在各清單檔裡提供對照。

---

## 系所代碼對照

`{semester}/courses/{代碼}.json` 的代碼。**代碼是字串不是數字**
（`"01"` 有前導零，`"C5"` 有英文字母），JSON 解析時不要當整數處理。

以下為 115-1 實際抓到的 60 個單位；學校新增單位時 `departments.json` 會即時反映，
這張表只是方便閱讀的快照。

#### 電資學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `31` | 電機系 | `36` | 電子系 |
| `59` | 資工系 | `65` | 光電系 |
| `82` | 電資學士班 | `99` | 電資外國學生專班 |
| `AY` | 太空所 | `C5` | 電資學院 |

#### 機電學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `2B` | 智動科 | `30` | 機械系 |
| `40` | 機電所 | `44` | 車輛系 |
| `45` | 能源冷凍空調系 | `56` | 製科所 |
| `61` | 自動化所 | `66` | 機電科所 |
| `81` | 機電學士班 | `A8` | 機電科技博士外生專班 |
| `AG` | 機械自動化外生專班 | `B2` | 半導體學士學位學程 |
| `B3` | 半導體外生專班 | `C0` | 機電學院 |

#### 工程學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `32` | 化工系 | `33` | 材資系 |
| `34` | 土木系 | `35` | 分子系 |
| `42` | 防災所 | `51` | 高分所 |
| `60` | 環境所 | `68` | 生化所 |
| `73` | 化工所 | `78` | 材料所 |
| `79` | 資源所 | `83` | 工程科技學士班 |
| `A0` | 能源光電外國學生專班 | | |

#### 管理學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `37` | 工管系 | `57` | 經管系 |
| `74` | 管理所 | `98` | 管理外國學生專班 |
| `AB` | 資財系 | `C2` | 管理學院 |

#### 設計學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `38` | 工設系 | `39` | 建築系 |
| `52` | 建都所 | `58` | 創新所 |
| `84` | 創意設計學士班 | `85` | 設計所 |
| `AC` | 互動系 | `AT` | 互動與創新外生專班 |

#### 人文與社會科學學院

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `49` | 技職所 | `54` | 英文系 |
| `91` | 科技法律學程 | `A4` | 智財所 |
| `A5` | 文發系 | | |

#### 創新前瞻科技研究學院

| 代碼 | 系所 |
|---|---|
| `C7` | 創新學院 |

#### 行政 / 校級單位（`college` 為 `null`）

| 代碼 | 系所 | 代碼 | 系所 |
|---|---|---|---|
| `01` | 教務處 | `10` | 體育室 |
| `14` | 通識中心 | `62` | 師資培育中心 |
| `AA` | 校院級課程 | | |

> `C0` `C2` `C5` `C7` 這種「學院」單位掛的是院級共同課程，
> 不是把該學院所有系的課集合起來。要一個學院的全部課程，
> 請照 `departments.json` 的 `colleges` 分組，逐系所取。

---

## 範例

### `meta.json`

```json
{
  "schema_version": 2,
  "generated_at": "2026-09-04T02:03:11Z",
  "source": { "name": "國立臺北科技大學 課程查詢系統", "url": "https://aps.ntut.edu.tw/course/tw/" },
  "disclaimer": "本資料由非官方爬蟲自動蒐集,僅供參考…",
  "latest": "115-1",
  "semesters": [
    {
      "year": 115, "sem": 1, "path": "115-1",
      "generated_at": "2026-09-04T02:03:11Z",
      "partial": false,
      "department_count": 60,
      "class_group_count": 286,
      "course_count": 2455,
      "merged_course_count": 310,
      "failed_department_count": 0
    }
  ],
  "endpoints": [
    { "path": "{semester}/teachers/{teacher_id}.json", "description": "教師課表" }
  ],
  "periods": [{ "code": "1", "start": "08:10", "end": "09:00" }],
  "requirement_symbols": [{ "symbol": "★", "required": false, "requirement_type": "專業選修" }]
}
```

`latest` 是目前最新的學年期。**不要把學年期寫死在前端**，讀這個欄位。

### `115-1/teachers.json` → `115-1/teachers/12095.json`

```json
{
  "teacher_count": 803,
  "teachers": [
    {
      "id": "12095",
      "name": "白敦文",
      "course_count": 3,
      "department_ids": ["59", "C5"],
      "path": "115-1/teachers/12095.json"
    }
  ]
}
```

```json
{
  "schema_version": 2,
  "year": 115, "sem": 1,
  "teacher": {
    "id": "12095",
    "name": "白敦文",
    "department_ids": ["59", "C5"],
    "url": "https://aps.ntut.edu.tw/course/tw/Teach.jsp?format=-3&year=115&sem=1&code=12095"
  },
  "course_count": 3,
  "courses": [ { "id": "364893", "name_zh": "數位影像處理", "...": "完整課程物件" } ]
}
```

**教師以代碼為準，不是姓名。** 115-1 有 803 個教師代碼但只有 801 個不同姓名 ——
確實有同名老師，用姓名分組會把兩個人的課混在一起。
程式對「有姓名但沒有 `Teach.jsp` 連結」的老師會輸出 `id` 與 `path` 為 `null`，
但實際資料裡沒有這種情形（90-1 至 115-1 抽查皆為 0 筆）——
沒有指定教師的課（115-1 有 504 門，多為班週會）根本不會產生教師條目。

### `115-1/classes.json` → `115-1/classes/2915.json`

```json
{
  "class_count": 286,
  "classes": [
    {
      "id": "2915",
      "name": "資工四",
      "department_id": "59",
      "department_name": "資工系",
      "college": "電資學院",
      "course_count": 53,
      "url": "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-4&year=115&sem=1&code=2915",
      "path": "115-1/classes/2915.json"
    }
  ]
}
```

明細檔結構與教師檔相同：`class_group` + `course_count` + `courses`。

> 班級代碼（`2915`）和系所代碼（`59`）是**兩組不同的 ID**，互相推不出來。

### `115-1/departments.json`

```json
{
  "schema_version": 2,
  "year": 115, "sem": 1,
  "departments": [
    {
      "id": "59",
      "name": "資工系",
      "college": "電資學院",
      "url": "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-3&year=115&sem=1&code=59",
      "class_groups": [
        { "id": "2915", "name": "資工四", "url": "…format=-4…code=2915" }
      ],
      "course_count": 53,
      "path": "115-1/courses/59.json"
    }
  ],
  "colleges": [
    { "name": "電資學院", "department_ids": ["31", "36", "59", "65", "82", "99", "AY", "C5"] },
    { "name": null, "department_ids": ["01", "10", "14", "62", "AA"] }
  ]
}
```

### `115-1/courses/59.json`

```json
{
  "schema_version": 2,
  "year": 115, "sem": 1,
  "department": { "id": "59", "name": "資工系", "college": "電資學院", "url": "…" },
  "course_count": 53,
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
      "time_slots": [{ "day": 5, "day_name": "五", "periods": ["2", "3", "4"] }],
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

### `115-1/index.json` / `index.json`

只放搜尋與篩選需要的欄位，細節到明細檔取。
`teacher_codes` / `class_ids` / `department_ids` 讓你能從搜尋結果直接跳到對應的明細檔。

```json
{
  "schema_version": 2,
  "covers": ["115-1", "114-2"],
  "course_count": 5264,
  "courses": [
    {
      "id": "364893",
      "name_zh": "數位影像處理",
      "teachers": ["白敦文"],
      "teacher_codes": ["12095"],
      "time_slots": [{ "day": 5, "day_name": "五", "periods": ["2", "3", "4"] }],
      "department_ids": ["59"],
      "class_ids": ["2915"],
      "credits": 3.0,
      "required": false,
      "requirement_type": "專業選修",
      "year": 115,
      "sem": 1
    }
  ]
}
```

頂層 `index.json` 只含 `covers` 列出的那幾個學期（目前是最新兩個）。
**更早的學期不在裡面**，請改讀 `{semester}/index.json` —— 那些檔案抓過一次
就不會再變，很適合長期快取。

### `115-1/programs.json` / `classrooms.json`

```json
{
  "program_count": 86,
  "programs": [
    { "name": "人工智慧科技學程", "course_count": 33, "course_ids": ["364892", "364893"] }
  ]
}
```

```json
{
  "classroom_count": 234,
  "classrooms": [
    {
      "id": "452",
      "name": "六教727(e)",
      "course_count": 12,
      "course_ids": ["364893"],
      "url": "https://aps.ntut.edu.tw/course/tw/Croom.jsp?format=-3&year=115&sem=1&code=452"
    }
  ]
}
```

學程只有中文名稱、沒有代碼，所以不另外開明細檔。

### `115-1/schedule.json`

```json
{
  "periods": [{ "code": "1", "start": "08:10", "end": "09:00" }],
  "days": [
    {
      "day": 3,
      "day_name": "三",
      "periods": [
        { "code": "2", "course_count": 118, "course_ids": ["361345", "361351"] }
      ]
    }
  ]
}
```

`periods` 已依 `1`–`9` → `N` → `A`–`D` 排好，**不是字典序**（字典序會把 `A` 排到 `9` 前面）。
沒有上課時間的課（體育、班週會）不會出現在這裡。

---

## 欄位說明

### 時間

`time_slots` 每個元素代表**一天**。`day` 是 0=日、1=一 … 6=六，
`periods` 是節次代碼，對應時間查 `meta.json` 的 `periods`。
沒有上課時間的課 `time_slots` 是空陣列。

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

每個 JSON 檔的頂層都有 `"schema_version"`。目前是 **2**。

- **新增欄位、新增端點不會升版。** 請用「忽略不認得的欄位」的方式寫你的程式。
- **移除欄位、改欄位型別、改欄位語意會升版**，並在此處說明。
- 升版時舊版路徑不保證保留 —— 這是免費的靜態檔案服務，請鎖定你測試過的版本號，
  發現 `schema_version` 變了就先檢查再上線。

### v2（2026-09-04）

為了容納 90 學年度起的歷史資料而做的兩個改動。**兩個都會影響既有使用端**：

| 改動 | 影響 |
|---|---|
| 頂層 `index.json` 只涵蓋**最新兩個學期**（原本是全部） | 要更舊的學期改讀 `{semester}/index.json`；新增 `covers` 欄位明講涵蓋範圍 |
| `generated_at` 只留在 `meta.json` 與 `errors.json` | 其他檔案不再有這個欄位。要知道某學期何時產生，讀 `meta.json` 的 `semesters[].generated_at` |

**為什麼要動：** 50 個學期全塞進一個 `index.json` 會膨脹到數十 MB；
而每個檔都帶時間戳的話，每次抓取後所有檔案內容都會變，發布時等於整包重推 ——
以歷史資料的量級來說那是每天好幾 GB 的無謂流量。
時間戳集中在 `meta.json` 粒度也更正確：一個學期的所有檔案本來就是同一次產生的。

## 更新頻率與學年期

**每 4 小時**自動跑一次（台灣時間 00、04、08、12、16、20 時）。
GitHub Actions 的排程在尖峰時段常延遲十幾分鐘，屬正常現象。

`meta.json` 的 `generated_at` 是該次產生的實際時間，請以它為準。

### 學年期是自動偵測的

爬蟲每次啟動會先讀學校首頁，看目前掛了哪幾個「上課時間表」入口，
再決定要抓哪些學期 —— **學年期沒有寫死在任何地方**。
115-1 過完換 115-2、再換 116-1，程式與 workflow 都不用改。

- **最新學期每次都重抓**（選課期間資料每天在動）
- 其他學期預設 **24 小時**才重抓一次（過去的學期幾乎不再變動，
  每 4 小時全部重抓只是白白增加學校的負擔）
- 學校首頁下架的舊學期**資料會留著**，只是不再更新

所以 `meta.json` 的 `semesters` 會越積越多。要最新的那個就讀 `latest`。

### 歷史學期只抓一次

首頁只掛最近兩個學期，但 `Subj.jsp?format=-2&year=&sem=` 不理會首頁 ——
**90 學年度起的資料都還在，而且版面 25 年沒變**（一樣是 23 欄，教師與教室
一樣是帶代碼的連結），所以同一套解析器直接吃得下。

這些歷史學期是用 `backfill` workflow 一次性抓下來的。它們不會再被重抓：

- 過去的學期資料不會變動，重抓沒有意義，也是對學校無謂的負擔
- 每 4 小時的例行抓取**完全不碰**歷史資料（發布時用 `keep_files`，
  只推當期的檔案），所以歷史資料的存在不會拖慢日常更新

如果哪個歷史學期抓壞了，重跑 `backfill` 並勾 `all_semesters` 就會重抓。

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
- **課號在一個學期內唯一，但「合開」的課各班級是不同課號。**
  同一個課號若出現在多個班級頁（通識、體育、校院級課程很常見），
  會合併成一筆，`classes` / `class_ids` / `department_ids` 列出全部。
  115-1 有 110 門課跨多個系所。
  但「合開」不是這種情況 —— 例如「數位影像處理」在資工四是 `364893`、
  在資工所是 `364899`，`notes` 都寫「資工四和資工所合開」，
  它們是兩筆獨立資料。**不要假設「同一門課 = 同一個課號」**，
  要判斷是否同一門課請一併看 `notes`。
- **`quota` 是原始頁面的「人」欄位**，會隨選課進度變動，只反映抓取當下的狀態。
  歷史學期的 `quota` 是**該學期結束後很久才抓的**，不代表當年選課時的即時人數。
- **歷史資料到 90 學年度為止。** 實測 85-1 只剩零星幾個單位、80-1 以前是空頁，
  不是抓取失敗而是系統本來就沒有。各學年度實際抓到幾個單位見 `meta.json`。
- **舊學期的系所代碼與現在不一定對得起來。** 系所會改名、合併、裁撤，
  代碼也會被回收。跨學期比較時請以各學期自己的 `departments.json` 為準，
  不要拿 115-1 的代碼表去套 95-1。
- 教學大綱內容尚未實作，目前只提供 `syllabus_url` 連結。

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
python -m crawler.main --out data/                       # 自動偵測學年期
python -m crawler.main --year 115 --sem 1 --out data/    # 指定學年期
python -m crawler.main --out data/ --dept 59 --pretty    # 開發用,只抓一個系所
python -m crawler.main --out data/ --no-cache --delay 1.5

# 回補歷史學期(已經抓過的會自動跳過,可以分批續跑)
python -m crawler.main --out data/ --years 90-114 --max-semesters 12
```

| 參數 | 說明 |
|---|---|
| `--year` / `--sem` | 指定學年期。**要嘛都給，要嘛都不給**；都不給就自動偵測 |
| `--refresh-after HOURS` | 非最新學期隔多久才重抓（預設 24 小時） |
| `--all-semesters` | 首頁列出的每個學年期都重抓，忽略 `--refresh-after` |
| `--years FROM-TO` | 回補模式：抓這個學年度範圍內**尚未抓過**的學期，例 `--years 90-114` |
| `--max-semesters N` | 這次最多抓幾個學期（回補分批用） |
| `--out DIR` | 輸出目錄，預設 `data/` |
| `--dept CODE` | 只抓指定系所（可重複）。輸出會是不完整的資料集 |
| `--delay` | 每次請求後的延遲秒數，**下限 0.5** |
| `--no-cache` | 略過 `.cache/`，強制重新抓取 |
| `--pretty` | 輸出縮排過的 JSON（檔案會變大） |
| `--log-level` | 記錄層級，預設 `INFO`。抓不到東西時開 `DEBUG` 看實際打了哪些 URL |

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
- 非最新學期預設 24 小時才重抓一次，不會每 4 小時把所有學期重掃一遍
- 歷史學期（回補下來的）抓過就永久跳過，25 年的資料只會被抓一次
- User-Agent 帶專案網址，方便校方辨識與聯絡
- 對方連不上時**整個停手**：連續 3 個網址重試到底仍然失敗，就判定學校端
  目前不可用，本次執行剩下的請求一律直接放棄、不再重試（見下）

課程網站抓太快很容易被封鎖。這些限制不是效能問題，是能不能長久運作的問題。

### 連線逾時與重試

runner 在美國，跨太平洋連學校主機，偶爾會整段路由抖掉。2026-09-04 08:33 UTC
那次排程就是連續 4 次 TCP connect 逾時、55 秒後整個 run 失敗，而同一時間
從台灣連是通的。所以：

| 設定 | 值 | 在 `crawler/config.py` |
|---|---|---|
| connect / read timeout | 30 秒 / 60 秒 | `TIMEOUT` |
| 一輪重試幾次 | 4（輪內退避 2 / 4 / 8 秒） | `RETRY_ATTEMPTS_PER_ROUND` |
| 總共幾輪 | 2 | `RETRY_ROUNDS` |
| 輪與輪之間等多久 | 180 秒 | `RETRY_ROUND_PAUSE` |
| 連續幾個網址全滅就判定站台不可用 | 3 | `UNAVAILABLE_AFTER` |

一輪打完全滅通常不是那個網址有問題，而是對方當下整個不可用。這種時候
密集重試既沒用也不禮貌，不如停下來等 3 分鐘再來一輪。

最後那道閘是配套：沒有它，「每個網址都撐很久」會讓一次全站中斷變成
幾百個網址各耗好幾分鐘、把 runner 燒到 job 逾時。判定不可用之後，
**抓到一半的學期不會被寫出去** —— 那些單位的「0 門課」是放棄去問，
不是真的沒開課，寫出去會蓋掉線上完整的資料，還會讓 `meta.json` 記成
「剛更新過」而擋掉之後 24 小時的重試。失敗會記進 `errors.json`，
下次排程重來。

### 架構

```
crawler/
  config.py         # 常數(base URL / schema 版本 / 延遲 / User-Agent)
  http.py           # 全專案唯一對外出口:限速、快取、重試、UTF-8 解碼
  models.py         # 輸出 schema 的 dataclass 定義
  periods.py        # 節次代碼對照與解析
  parse_util.py     # 解析器共用工具
  parse_semester.py # 解析 course.jsp — 目前有哪些學年期(純函式)
  parse_dept.py     # 解析 format=-2 / -3(純函式,不連網)
  parse_course.py   # 解析 format=-4(純函式,不連網)
  output.py         # 把 CrawlResult 寫成各維度的 JSON
  main.py           # CLI:選學期、抓取流程、去重

tests/              # 全離線,對 tests/fixtures/ 的真實 HTML 樣本斷言
scripts/            # 一次性的偵察腳本(recon*.py),不參與正式流程,
                    # 留著是為了保存「當初怎麼確認的」這件事
.github/workflows/  # crawl(每 4 小時)、backfill(手動回補)、test(每次 push)
web/index.html      # 發布到 gh-pages 根目錄的說明頁,由 workflow 複製過去
```

解析器一律是**純函式**：吃 HTML 字串 → 吐 dataclass，不發網路請求。
所有網路存取都集中在 `http.py`，限速與快取的規則只要在那裡守住就守住了。

完整的開發規格與偵察紀錄見 [`plan.md`](plan.md)。
