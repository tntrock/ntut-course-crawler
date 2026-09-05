# ntut-course-crawler

[![test](https://github.com/tntrock/ntut-course-crawler/actions/workflows/test.yml/badge.svg)](https://github.com/tntrock/ntut-course-crawler/actions/workflows/test.yml) [![crawl](https://github.com/tntrock/ntut-course-crawler/actions/workflows/crawl.yml/badge.svg)](https://github.com/tntrock/ntut-course-crawler/actions/workflows/crawl.yml)

國立臺北科技大學課程資料的**非官方**靜態 API。

每 4 小時自動爬取學校課程查詢系統，轉成結構化 JSON 後發布到 GitHub Pages。
沒有伺服器、沒有資料庫、不需要 API key —— 直接 `fetch()` 就能用。

資料依**系所 / 教師 / 班級 / 學程 / 教室 / 時段**分別建索引，
查一位老師的課不必先下載全校兩千多門課。

涵蓋 **90-1 至 115-1 共 51 個學期、141,018 門課**（民國 90 年起，約 25 年）。
課程數是 2026-09-05 的實測值，選課期間會小幅變動；即時數字請讀 `meta.json`
的 `semesters[].course_count`。

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
| **退選率**、修課人數 | 課程物件的 `enrolled` / `withdrawn`（見下） | |
| 退選率**隨時間的走勢** | `enrollment.json` | |
| **某門課的教學大綱**（進度、評量、教材、SDGs） | `{semester}/syllabus/{課號}.json` | `115-1/syllabus/364893.json` |

不知道代碼？先拉 `{semester}/departments.json`、`teachers.json`、`classes.json`，
每一筆都附了 `path` 欄位，直接接在 base URL 後面就是明細檔的網址。

## 端點總覽

| 路徑 | 內容 | 115-1 實際大小 |
|---|---|---|
| `meta.json` | 學年期清單、端點清單、節次與必選修對照 | 13 KB |
| `index.json` | **最新兩個學期**的課程輕量索引 | 1.5 MB（gzip 149 KB）|
| `errors.json` | 各學期抓取失敗的單位（目前 51 個學期全部為空） | < 1 KB |
| `changes.json` | 最近的課程與教師異動事件（見下） | 隨異動量變動 |
| `enrollment.json` | 修課 / 撤選人數逐日快照的索引（見下） | 隨天數變動 |
| `{semester}/enrollment/{date}.json` | 某一天的逐課修課 / 撤選人數 | 約 120 KB / 天 |
| `syllabus.json` | 教學大綱的抓取進度與逐課狀態（見下） | 隨課數變動 |
| `{semester}/syllabus/{course_id}.json` | 單一課程的教學大綱與進度 | 約 3–8 KB / 門 |
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
| `bulk_change` | 一次異動量太大，折成摘要 | `counts`、`by_department`、`by_class`、`samples` |

- **`at` 是偵測到的時間，不是學校異動的時間** —— 實際異動發生在上一次抓取
  與這次之間，最多相差 4 小時。
- **加欄位不算異動。** 比對只看兩邊都有的欄位 —— 索引的欄位會隨程式演進而
  增加，新欄位第一次出現時舊索引裡根本沒有那個 key，把它當成「從 null 變成
  某值」會讓全校幾百門課一次全部變成 `course_changed`。那不是學校動了資料。
- **事件是 append-only 的，寫下去就不會再改。** 所以舊事件可能缺少後來才加的
  欄位（例如 2026-09-04 10:24 之前寫入的 `bulk_change` 沒有 `by_department` /
  `by_class` / `samples`）。**使用端請把 `type` 以外的欄位一律當成選填**，
  缺了就降級顯示，不要假設一定存在。
- `checked_at` 是最後一次比對的時間，**沒有異動時也會更新**。所以
  「`checked_at` 是今天但沒有新事件」＝學校真的沒動；
  「`checked_at` 停在三天前」＝爬蟲沒在跑。
- `course_changed` 比對的欄位是課名、教師、時段、學分、必選修、開課系所、班級。
- **教師事件是用教師代碼比對的**，不是姓名 —— 115-1 實測 803 個代碼只對到
  801 個姓名，確實有同名老師。換人授課時課還在，只有教師端看得出少了誰。
- 最多保留 500 筆事件。
- **只有最新兩個學期會產生事件** —— 比對基準是頂層 `index.json`，它只涵蓋
  那兩個。第一次抓到某個學期時記成 `baseline`，不會變成「新增兩千多門課」。
- 單次超過 200 筆異動會折成一筆 `bulk_change`，避免把先前真正的異動整個
  推出保留範圍。摘要會帶**依系所與班級的分組統計**加上前 10 筆完整樣本，
  所以不必再自己去 diff。2026-09-04 實際遇到的那次長這樣：

  ```json
  {
    "type": "bulk_change", "event_count": 267,
    "counts": { "course_added": 265, "course_removed": 2 },
    "by_department": { "01": 185, "14": 80, "36": 1, "37": 1 },
    "by_class": { "589": 80, "2519": 42, "2520": 38, "2522": 35,
                  "2524": 32, "2523": 28, "2521": 10, "…": 1 },
    "samples": [ { "type": "course_added", "id": "367177",
                   "name": "全球化與多元文化", "class_ids": ["589"] } ]
  }
  ```

  兩個單位、7 個班級 —— 一眼就看得出是學校開了一批跨校選課（`589` 博雅選修
  跨校、`2519`–`2524` 與北大 / 北醫 / 海大的跨校選課），而不是解析器壞了。
  真的抓歪時分組會散落在幾十個系所，一樣一眼分得出來。
  `by_department` 全列（系所頂多 60 個），`by_class` 只留量最大的 20 個。
  一門課可能同時掛多個系所或班級，所以分組數量的總和會大於 `event_count`。

### 修課人數與退選率

學校的課程表本身就有「人」和「撤」兩欄，所以**不必去抓教學大綱**：

| 欄位 | 意思 |
|---|---|
| `enrolled` | 修課人數（原始頁面的「人」欄） |
| `withdrawn` | 撤選人數（原始頁面的「撤」欄） |
| `quota` | `enrolled` 的**舊名，已棄用**，值完全相同 |

> `quota` 這個名字會讓人以為是名額上限，其實一直都是修課人數 ——
> 114-2 全校有 111 種不同的值、只有 21% 是 5 的倍數，名額上限不會長這樣。
> 舊欄位保留不刪（見相容性承諾），新的程式請用 `enrolled`。

兩個欄位在**課程物件**（`{semester}/courses/`、`teachers/`、`classes/` 的明細檔）
和**輕量索引**（`index.json`、`{semester}/index.json`）裡都有，所以算全校退選率
不必先下載 60 個系所明細檔。

#### 分母要用哪一個

`人` 到底是撤選**後**的現存人數，還是**含**撤選的原始人數，學校沒有說明，
從資料本身也判斷不出來。兩種算法在全校層級差很小，但對單一課程差很多：

```
撤 / 人          ← 若「人」不含撤選的人
撤 / (人 + 撤)   ← 若「人」是撤選後的現存人數
```

114-2 全校實測：修課 73,403 人次、撤選 1,596 人次 →
兩種算法分別是 **2.17%** 與 **2.13%**。但那門撤選 17 人、現存 30 人的
「物件導向程式設計」，兩種算法是 56.7% 和 36.2%，差很多。

要判定的話：115-1 的撤選期還沒到（「撤」目前全是 0），
等撤選開始後看某門課的 `enrolled` 會不會隨 `withdrawn` 上升而下降就知道了。
`enrollment.json` 的逐日快照正是為了留下這段資料。

### `enrollment.json`：人數的時間軸

明細檔裡的人數是**當下**的值，每次抓取直接覆蓋。學期結束後那是定案的數字，
拿來算退選率沒問題；但「哪門課在第幾週被大量退掉」這種問題，快照沒留下來
就永遠答不了，而且錯過了要再等一個學期。

所以每天存一份（依台灣時區切日，同一天內重複抓取就覆蓋當天那份）：

```json
{
  "snapshot_count": 12,
  "snapshots": [
    { "semester": "115-1", "date": "2026-09-05",
      "at": "2026-09-05T12:03:11Z",
      "course_count": 2717,
      "enrolled_total": 73403, "withdrawn_total": 1596,
      "path": "115-1/enrollment/2026-09-05.json" }
  ]
}
```

- 根目錄的 `enrollment.json` 只放**每天的總計**，全校退選率的走勢靠它就畫得出來。
- 要看是哪幾門課，再去拿當天的 `{semester}/enrollment/{date}.json`，
  裡面是逐課的 `id` / `enrolled` / `withdrawn`。
- 逐日快照只新增、不改寫，最多保留 400 筆索引。

> **人數的變動刻意不進 `changes.json`。** 加退選期間每 4 小時就有上千門課的
> 人數在動，塞進事件流會每次都觸發 `bulk_change`，把真正的結構性異動
> （加開、停開、調課、換老師）整個淹掉 —— 兩種資料的變動頻率差一個數量級。

### 教學大綱

課程物件的 `syllabus_url` 是學校原始頁面的網址；
`{semester}/syllabus/{課號}.json` 是**解析過的內容**：

```json
{
  "course_id": "364893", "course_name": "數位影像處理",
  "teachers": ["白敦文"], "department_ids": ["59"],
  "url": "https://aps.ntut.edu.tw/course/tw/ShowSyllabus.jsp?snum=364893&code=12095",
  "updated_at": "2026-08-11T01:00:23Z",
  "has_content": true,
  "content_hash": "3f8a1c04b27de591",

  "teacher_name": "白敦文", "teacher_email": "twp@ntut.edu.tw",
  "outline": "This course will be lectured in Chinese/English…",
  "schedule": "1. Introduce to Image Processing\nAI applications\n…",
  "flexible_learning": {
    "category": ["學生分組實作及討論", "AI 輔助學習與工具應用", "遠距教學"],
    "content": "…", "hours": 3, "outcome": "…", "assessment_ratio": "…"
  },
  "assessment": "Quiz:15%, Homework:15%, Midterm 35%, Final 35%.",
  "materials": "…", "contact": ["研究室分機(Extension): 4222", "…"],
  "extended_resources": ["混成學習 (Blended Learning)", "…"],
  "sdgs": ["SDG4：優質教育（Quality Education）", "…"],
  "ai_usage": ["使用生成式AI作為教學過程的輔助工具", "…"],
  "notes": "…"
}
```

- `updated_at` 是**老師最後修改大綱的時間**（學校顯示台灣時間，這裡轉成 UTC，
  跟全站其他時間戳一致）。
- `content_hash` 是這份內容的指紋（sha256 前 16 個 hex 字）。**內容沒變就不會重寫這個檔**，
  所以拿它當快取鍵是安全的。我們抓下來的時間不在這個檔裡 —— 見下面的 v3 說明。
- `has_content` 為 `false` 代表老師還沒填。檔案仍然會產生 —— 沒有它的話，
  每次執行都會再去問一次同一頁。
- 條列式的欄位（`sdgs`、`ai_usage`、`contact`、`extended_resources`）輸出成陣列，
  項目符號已去掉。長文欄位（`outline`、`schedule`、`materials`）保留原樣的換行，
  不自作聰明切開。
- **認不得的欄位不會被丟掉**，會原樣收進 `extra`。學校加新欄位時
  （SDGs 和「是否導入 AI」顯然就是近年才加的）才不會靜靜漏抓半年。

#### 為什麼大綱獨立成一支、一天兩班

大綱是**一門課一頁**，塞不進每 4 小時一次的例行抓取，所以獨立成
`syllabus` workflow，一天排兩班（UTC 01:00 / 13:00 = 台灣 09:00 / 21:00）。

**兩班都會真的全跑一輪**，所以大綱最多落後半天。開學前老師大量在改，半天的差別有意義。

重抓週期因此設 **6 小時**，而不是「比 12 小時間隔小一點」的 10 —— Actions 的 cron 只保證「不早於」，
實測會遲 2～4 小時，前一班遲到、後一班準時的話實際間隔會被壓成 8 小時甚至更短，
週期定 6 才保證壓縮後兩班都還是會抓。

順帶也就有了備援：前一班若遇上學校斷線整批失敗，當天稍後那班就補得回來，不必等到隔天。
（2026-09-04 就發生過：學校清晨掛了一小時，那天的大綱整批沒更新。）

2026-09-04 用 50 頁實測出 **1.20 秒/頁**（延遲 1 秒 + 學校回應），據此：

| | 請求數 | 時間 |
|---|---|---|
| 課程抓取（沒有它就不知道每門課的大綱網址） | 355 | 8.4 分 |
| 教學大綱（2,717 門課裡有 **1,909 門**有大綱連結） | 1,909 | 38.3 分 |
| **合計** | **2,264** | **約 47 分** |

- 沒有 `syllabus_url` 的課直接跳過 —— 跨校選課那類在北科系統裡沒有大綱連結，
  所以是 1,909 門而不是 2,717 門
- 每抓一門就落地，中途失敗不會賠掉已經抓好的；下次執行接著抓
- `syllabus.json` 記著每門課上次抓的時間與內容雜湊，重抓週期預設 **6 小時**
  （`--syllabus-refresh-after`）—— 必須小於兩班被排程延遲壓縮後的間隔，
  否則上一班抓過的這一班全被跳過
- `--max-syllabus N` 可以只抓一批（冒煙測試用），預設 `0` = 不限

> **為什麼不分批。** 分批（每天 800 頁、2.4 天輪完）省不了多少，卻讓重抓週期
> 得設成 30 天，資料最多落後一個月。而實測發現開學前老師正在大量修改大綱 ——
> 50 份樣本的 `updated_at` 橫跨 2026-06-01 到抓取前兩小時。落後一個月不能接受。

例行的 `crawl` workflow **完全不碰大綱**，維持 355 個請求、8 分鐘跑完。
每天的總請求數約 6,660 次（4 小時排程 6 × 355，加大綱那支兩班的 2 × 2,264），
仍然是單執行緒、每次請求後 sleep 1 秒 —— 抓取禮儀一條都沒有放寬。

#### 大綱涵蓋哪些學期

課表回補到 90 學年度，**大綱只補到 110-1**。往前是可以補的，往前補到底則不划算：

| 學期 | 有大綱連結 / 總課數 | |
|---|---|---|
| 115-1 | 1,911 / 2,717 | 70.3% |
| 110-1 | 2,302 / 3,084 | 74.6% |
| 100-1 | 2,173 / 2,777 | 78.2% |
| 96-1 | 1,908 / 2,591 | 73.6% |
| **95-2** | 291 / 2,462 | **11.8%** ← 斷層 |
| 90-1 | 17 / 2,428 | 0.7% |

2026-09-05 掃過 gh-pages 上全部 51 個學期的統計。**96-1 是學校那邊的分界線**：
96-1 起每學期都有兩千多門課掛著大綱連結，95-2 以前塌到 12% 以下，
而且實抓一頁確認過那些是**空殼** —— HTTP 200、只有課號課名那張表，
內容欄位全空（`snum=100001` 那頁 3.7 KB，解析結果是 `{}`）。
96-1 到 110-1 之間的大綱是完整的（實抓 110-1 一頁驗證過，教學目標、每週進度、
評分方式、教材都在），要往前補只要把 `backfill` 的 `years` 往前調即可。

補到 110-1 是容量上的選擇：11 個學期約 24,000 份大綱，估計讓 gh-pages 從
44 MB 長到 100 MB 上下；補到 96-1 則是 40 個學期、八萬多份，會逼近 300 MB。

#### 為什麼大綱檔沒有「抓取時間」

`fetched_at` 在 v2 的時候是放在每份大綱檔裡的，那是個很貴的錯誤：
一天兩班、每班 1,909 份，即使老師一個字都沒改，git 也會收下 1,909 個新 blob。
gh-pages 一天長 1.2 MB，八成是這樣來的。

v3 改成比對**內容雜湊**：

- 大綱檔帶 `content_hash`，不帶任何時間戳
- 抓下來算一次雜湊，跟 `syllabus.json` 裡記的比對，**一樣就整個不寫那個檔**
- 沒寫的檔不在 `publish_dir` 裡，`keep_files: true` 會讓遠端那份原封不動留著

所以學期中大綱不動的日子，兩班大綱跑完的 git 增量接近零。
「這份是什麼時候抓的」改到 `syllabus.json` 裡查。

#### `syllabus.json` 的三個區塊

```json
{
  "schema_version": 3,
  "semesters": [
    {"semester": "115-1", "fetched": 1909, "with_url": 1909, "course_count": 2717,
     "oldest_fetch": "2026-09-05T06:01:09Z", "newest_fetch": "2026-09-05T06:41:05Z"},
    {"semester": "114-2", "fetched": 1968, "with_url": 1968,
     "frozen": true, "frozen_at": "2026-09-06T02:11:40Z"}
  ],
  "fetched": {
    "115-1": {"360744": {"at": "2026-09-05T06:01:09Z", "hash": "3f8a1c04b27de591"}}
  },
  "frozen": {"114-2": {"fetched": 1968, "with_url": 1968, "at": "2026-09-06T02:11:40Z"}}
}
```

- `semesters` 是給人看的進度，一個學期一列
- `fetched` 是給下一次執行看的逐課狀態（抓取時間 + 內容雜湊）
- `frozen` 是**已經補完、逐課狀態收掉了**的歷史學期，只留門數

收合是為了大小：一門課的狀態約 66 bytes，11 個學期兩萬多筆就是 1.6 MB，
而這個檔每次抓大綱都會整個重寫 —— 那等於把剛用雜湊省下來的 blob
換一種形式吐回去。過去的學期不會再變動，一個門數就夠了。

**收合過的學期，`crawl_syllabi` 會整個跳過**，連 targets 都不算。
真要重抓，手動把該學期從 `frozen` 裡刪掉再跑一次 `backfill`。

> 判斷「這學期還會不會變」的依據是 meta.json 裡最新的那個學年期。
> 只有最新學期會被重抓；歷史學期一律只補沒抓過的，`--syllabus-refresh-after`
> 對它們無效。沒有這條規則的話，補完 110-1 之後每一班都會想重抓兩萬多頁。

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
  "schema_version": 3,
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
  "schema_version": 3,
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
  "schema_version": 3,
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
  "schema_version": 3,
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
      "enrolled": 22,
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
  "schema_version": 3,
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
      "language": null,
      "enrolled": 22,
      "withdrawn": 0,
      "year": 115,
      "sem": 1
    }
  ]
}
```

索引裡的 `language` 讓「只看全英語授課」不必下載 60 個系所明細檔
（115-1 全校 2,717 門裡有 499 門非中文：英語 488、中英雙語 11）。
`enrolled` / `withdrawn` 同理，是為了讓算退選率只需要一個檔。

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

### 授課語言

`language` 空白（`null`）代表**中文授課**，不是「未知」——
學校只在非中文的課填這一欄。115-1 全校的實際值只有 `"英語"`（488 門）
與 `"中英雙語"`（11 門）兩種，其餘 2,218 門都是 `null`。

所以「找全英語授課」的條件是 `language === "英語"`，
「找中文授課」是 `language === null`。

### 空值

原始頁面用全形空白 `　`(U+3000)表示「沒有這個欄位」，
輸出時一律正規化成 `null`（陣列欄位則是 `[]`）。

---

## `schema_version` 與相容性承諾

每個 JSON 檔的頂層都有 `"schema_version"`。目前是 **3**。

- **新增欄位、新增端點不會升版。** 請用「忽略不認得的欄位」的方式寫你的程式。
- **移除欄位、改欄位型別、改欄位語意會升版**，並在此處說明。
- 升版時舊版路徑不保證保留 —— 這是免費的靜態檔案服務，請鎖定你測試過的版本號，
  發現 `schema_version` 變了就先檢查再上線。

### v3（2026-09-05）

只影響教學大綱，把 v2 沒做完的那半件事做完。

| 改動 | 影響 |
|---|---|
| `{semester}/syllabus/{課號}.json` 不再有 `fetched_at` | 要知道何時抓的，讀 `syllabus.json` 的 `fetched[學期][課號].at` |
| 同一個檔新增 `content_hash` | 加欄位，不影響既有使用端 |
| `syllabus.json` 的 `fetched[學期][課號]` 從字串變成物件 | 原本是抓取時間字串，現在是 `{"at": ..., "hash": ...}` |
| `syllabus.json` 新增 `frozen` 區塊 | 已補完的歷史學期只留門數，逐課狀態不再保留 |

**為什麼要動：** 跟 v2 拿掉 `generated_at` 完全同一個理由，只是漏了大綱這一塊。
`fetched_at` 每次抓取都變，一天兩班、每班 1,909 份大綱，即使內容一個字都沒改，
git 也會收下 1,909 個新 blob。改成比對內容雜湊之後，沒變就不重寫那個檔，
配合 `keep_files: true`，遠端那份原封不動留著。

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

| workflow | 頻率 | 台灣時間 |
|---|---|---|
| `crawl` | 每 4 小時 | 00、04、08、12、16、20 時 |
| `syllabus` | 一天兩次 | 09、21 時 |
| `crawl`（整包重建） | 每月一次 | 1 號 14 時 |
| `backfill` | 只手動 | 補歷史學期的課表與大綱 |

**別把 cron 的時間當成實際執行時間。** GitHub Actions 只保證「不早於」，
這個 repo 實測常遲 **2～4 小時** —— 2026-09-04 排在 UTC 19:00 的大綱那班遲了
2 小時 20 分，從台灣清晨 3 點漂到 5 點 20，正好撞上學校站台掛掉的那一小時，
整批失敗。所以「挑離峰時段避開學校維護」這種做法在這裡是控制不了的，
真正的保險是整批重試（見下）加上一天兩班互為備援。

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
- 已經結束的學期不會產生「今天的」人數快照 —— 那個學期的數字早就定案了，
  在時間軸上多一個今天的轉折點只會誤導

如果哪個歷史學期抓壞了，重跑 `backfill` 並勾 `all_semesters` 就會重抓。

**補大綱**勾 `with_syllabus`。判斷「抓過了沒」會跟著變嚴：課表有了但大綱還沒抓的
學期會重新排進來，並且先重抓一次課表（每門課的大綱網址只有課表頁面上才有）。
一個學期因此要約 51 分鐘而不是 9 分鐘，所以 `max_semesters` 留空時會自動用 3。
補完的學期會在 `syllabus.json` 的 `frozen` 裡登記，之後永遠跳過。

### 每月一次的整包重建

`keep_files` 有兩個代價，都靠 `crawl` 的 `rebuild` 模式收拾 ——
**每月 1 號台灣時間 14 時自動跑一次**，也可以手動勾 `rebuild` 提前觸發：

- **gh-pages 的 commit 歷史只增不減。** 2026-09-05 實測每次發布讓 pack 多約
  1.2 MB，一天八班（六次 `crawl` + 兩次 `syllabus`）就是一年 2 GB 起跳，
  幾個月就會頂到 GitHub 建議的 1 GB。`rebuild` 用 `force_orphan` 重發布，
  把歷史壓成單一 commit
- **幽靈檔。** 班級或教師從學校系統消失時，`keep_files` 會把它們的舊檔留在
  遠端，變成永遠不會更新的資料。`rebuild` 先把整包 gh-pages 撈回本機再整包
  重發，那些檔就跟著消失

重建那班一樣只重抓當期，歷史學期是從 gh-pages 撈回來原樣帶過去的 ——
壓的是 git 歷史，不是資料。

`force_orphan` 是不可逆的，所以這條路徑上有兩道保險：撈回整包那一步失敗
**直接讓 job 死掉**（不做「優雅降級」——那會讓 `data/` 只剩當期學期，
發布出去等於銷毀 51 個學期的資料和唯一能救回來的 commit 歷史）；
發布前再比對一次學期數與檔案數，掉了就中止。寧可這個月不重建。

---

## 資料範圍與已知限制

- **只涵蓋課程查詢系統的「上課時間表」**（`Subj.jsp?format=-2` 那棵樹）。
  該系統每個學期只提供這一個上課時間表入口，因此這棵樹爬完即無遺漏。
  暑期課程（`Summer.jsp`）、學程查詢、教室使用情形不在本專案範圍。
- **教學大綱有抓**（見上），一天全量更新兩次，所以最多落後半天。
  確切時間看 `syllabus.json` 的 `fetched[學期][課號].at`；
  老師最後修改的時間是該檔的 `updated_at`。
  **大綱只涵蓋 110-1 以後**，更早的學期只有課表。
- 學程查詢系統不在範圍內，所以查得到「無人機微學程有哪些課」，
  查不到「修完幾門才算完成該微學程」。
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
- **`enrolled` / `withdrawn` 是原始頁面的「人」「撤」欄位**，會隨選課進度變動，
  只反映抓取當下的狀態。歷史學期的值是**該學期結束後很久才抓的**，
  等於是最終定案的數字，不代表當年選課期間的即時人數。
  想看變動過程請用 `enrollment.json`（只有開始記錄之後的學期才有）。
  `quota` 是 `enrolled` 的舊名，已棄用但保留。
- **歷史資料到 90 學年度為止。** 實測 85-1 只剩零星幾個單位、80-1 以前是空頁，
  不是抓取失敗而是系統本來就沒有。各學年度實際抓到幾個單位見 `meta.json`。
- **舊學期的系所代碼與現在不一定對得起來。** 系所會改名、合併、裁撤，
  代碼也會被回收。跨學期比較時請以各學期自己的 `departments.json` 為準，
  不要拿 115-1 的代碼表去套 95-1。
- **教學大綱是一天全跑兩次抓下來的**，`updated_at` 是老師在學校系統上最後
  修改的時間（已轉成 UTC）。看不懂的日期格式會原樣保留，不保證是 ISO 8601。
  沒有 `syllabus_url` 的課（跨校選課那類）不會有 `{semester}/syllabus/` 檔案。

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
| `--with-syllabus` | 順便抓教學大綱（一門課一頁，很慢，預設關閉） |
| `--max-syllabus N` | 這次最多抓幾頁大綱，預設 `0` = 不限（全校一輪約 38 分鐘） |
| `--syllabus-refresh-after H` | 同一門課的大綱隔多久重抓，預設 `6` 小時（配合一天兩班各全跑一輪）。**只對最新學期有效**，歷史學期一律只補沒抓過的 |
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
| 整批重跑幾次 / 間隔多久 | 3 次 / 20 分鐘 | `crawl.yml` 的 `MAX_ATTEMPTS`、`RETRY_WAIT_SECONDS` |

一輪打完全滅通常不是那個網址有問題，而是對方當下整個不可用。這種時候
密集重試既沒用也不禮貌，不如停下來等 3 分鐘再來一輪。

#### 整批重跑（workflow 層級）

上面那套治得了線路抖動，治不了**幾十分鐘等級的中斷**。2026-09-04 08:33 UTC
那次，GitHub runner 完全連不到學校，持續了近一小時，而同一時間從台灣連只要
17 毫秒 —— 撐 7.5 分鐘救不了，只能等。

所以 `crawl.yml` 在 job 層級再包一層：**抓取失敗就等 20 分鐘整批重來，最多
三次**，涵蓋約 55 分鐘的時窗。三次都失敗就放棄，交給四小時後的下一班 ——
中斷若超過一小時，讓 runner 空轉沒有意義。

重試很便宜：`.cache/` 在同一個 job 內共用，前一次已經抓到的頁面不會再打一次，
所以「抓到一半失敗」的重試只補剩下的部分。中斷期間封包在防火牆就被丟了，
對學校也沒有額外負擔。

`syllabus.yml` 也包了同一層，但放寬成**等 30 分鐘、最多五次**（涵蓋約 2.5 小時）。
理由是班次密度不同：`crawl` 一天六班，漏一班四小時後就補回來；大綱一天只有兩班，
漏一班就是半天沒資料。2026-09-04 那次學校斷線剛好超過一小時，舊的三次 × 20 分鐘差一點就撐過去了。

`backfill.yml` 沒有加這一層 —— 它本來就會跑一個多小時，再加三次重試有機會
撞到 6 小時的 job 上限；而且它是手動觸發、抓過的學期會永久跳過，重跑一次
就會從斷掉的地方接續下去，本來就是可重入的。

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
.github/workflows/  # crawl(每 4 小時)、syllabus(一天兩班)、
                    # backfill(手動回補)、test(每次 push)
web/index.html      # 發布到 gh-pages 根目錄的說明頁,由 workflow 複製過去
```

解析器一律是**純函式**：吃 HTML 字串 → 吐 dataclass，不發網路請求。
所有網路存取都集中在 `http.py`，限速與快取的規則只要在那裡守住就守住了。

完整的開發規格與偵察紀錄見 [`plan.md`](plan.md)。
