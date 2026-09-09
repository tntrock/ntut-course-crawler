# 開發參考

**這是接手這個 repo 唯一要讀的工作文件。** 取代了原本的 `plan.md`（開發規劃書）
與 `HANDOVER.md`（交接文件），兩者已刪除。

檔案分工：

| 檔案 | 給誰看 | 內容 |
|---|---|---|
| `README.md` | **資料的使用者** | 有哪些端點、欄位是什麼意思、相容性承諾 |
| `reference.md`（本檔） | **改這個 repo 的人** | 硬性規定、實測過的事實、環境地雷、決策紀錄 |

原則：**只寫讀 code 讀不出來的東西。** 程式怎麼運作看程式，這裡寫的是
「為什麼是這樣」「踩過什麼坑」「哪些事不要做」。

---

## 1. 絕對不要做的事

這幾條是硬性規定，**不接受為了「快一點」而放寬**。程式碼裡多處註解引用本節。

### 抓取禮儀

參考既有專案 [gnehs/ntut-course-crawler-node](https://github.com/gnehs/ntut-course-crawler-node)
作者的說明：「課程網站若抓取過快很容易被封鎖」。

- **單執行緒。** 不使用 `threading` / `asyncio` / `multiprocessing` 平行抓取。
- **每次請求後 sleep**，預設 1.0 秒，**下限 0.5 秒**，不得調低。
  下限實作在 `resolve_delay()`，參數與環境變數都繞不過。
- **User-Agent 要有辨識度並附聯絡方式**（`config.py`）。
- **開發期一律用 fixture 或快取**，不要為了驗證反覆打學校伺服器。
  真的需要新樣本時**一次只抓一頁並說明理由**。

測試有一道安全網：`tests/conftest.py` 的 `no_real_network` fixture 會讓任何
發出真實 HTTP 請求的測試直接失敗。**不要拆掉它。**

### 憑證

Python 內建 `ssl`（OpenSSL 3.x 嚴格模式）會拒絕學校憑證：
`CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier`。但 curl 與瀏覽器
都通過——憑證只是缺了非必要的擴充欄位，不是真的中間人風險。

**解法是 `truststore`**（`http.py` 已處理，必須在 `import requests` 之前注入），
讓 Python 走系統憑證庫。**不要用 `verify=False`**，那是把問題蓋掉不是解掉。

> Linux + Python 3.12 已驗過沒問題（2026-09-03 第一次 workflow 冒煙測試），
> 不需要為 CI 分支處理。

### 不要猜 HTML

第 2 節列的站台特性都是**實測結果**。與現況不符時以 fixture 為準並回報差異，
不要憑印象改解析器。解析器遇到非預期結構時**降級處理（填 `None` + warning）
而不是拋例外**——單一系所壞掉不該拖垮整批。

讀不到的值一律 `None`，**不要填 0**。「沒讀到」和「零」是兩回事。

---

## 2. 站台結構（實測過的事實）

系統首頁 `https://aps.ntut.edu.tw/course/tw/course.jsp`，傳統 JSP + 表格排版，
無前端渲染，UTF-8。

> 頁面裡那行 `<meta charset=UTF-8>` **被 HTML 註解包住**，不要拿它判斷編碼。
> 直接 `content.decode("utf-8")` 就對了。

```
Subj.jsp?format=-2                    ← 學院 / 行政單位總覽
  └─ Subj.jsp?format=-3&code={單位}    ← 該單位底下的班級列表
       └─ Subj.jsp?format=-4&code={班級} ← 課程列表(每列一門課)
            └─ ShowSyllabus.jsp?snum=&code=  ← 教學大綱
```

其他入口：`Teach.jsp?format=-3`（教師授課時數）、`Croom.jsp?format=-3`
（教室使用情形與**容量**）、`Cprog.jsp?format=-5`（必選修符號對照）。

### 三個陷阱

1. **總覽頁第一個連結不是系所。** `format=-2` 第一列是行政單位（教務處 `01`、
   體育室 `10`、通識中心 `14`、師培中心 `62`、校院級 `AA`），往下爬只會拿到
   「遠距教學班」這類班群。**不要拿第一個連結當樣本。**
2. **`code` 在 `-3` → `-4` 之間會換一組。** 系所代碼是兩碼英數（`59`），班級
   代碼是四位數字（`2915`），兩者無法互推。**一定要從 `-3` 頁面解析出完整
   連結，不可自己拼 URL。**
3. **同一門課會出現在多個班級頁。** 合開課程的同一課號會在兩個 `format=-4`
   頁面各出現一次，輸出前要**依課號去重**並保留它隸屬的所有班級。

### 解析上的細節

- **空欄位是全形空白 U+3000**，用 `parse_util.clean()` 處理，不要只 `strip()`。
- **HTML 一律用 `soup_of()`（lxml）。** 這站的 `<tr>`/`<td>` 沒有收尾標籤，
  `html.parser` 會切錯表格。
- **`★` 與 `☆` 都是選修**（依學校課程標準頁確認，不是「★=必修」）。
- **沒有英文課名。** 課程列表頁與大綱頁都沒有，`Course.name_en` 恆為 `null`，
  保留欄位只是維持契約穩定。
- **教師代碼才是 key，不是姓名。** 115-1 實測 803 個代碼只對到 801 個姓名，
  確實有同名老師。
- **進修部沒有另一棵樹。** 首頁每學期只有一個「上課時間表」入口，抽查機械系
  與資工系的 `format=-3` 都沒有夜間班。`format=-2` 這棵樹就是全部。
- **`enrolled` 是修課人數，不是名額上限。** 想算「修課人數 vs 座位數」的分母
  要用 `capacity.json`。

### 資料範圍

學校首頁列到 **90-1 ~ 115-1 共 51 個學期**，再往前就沒有了。

教學大綱的連結率隨年代遞減，但**內容是真的**（2026-09-07 實抓兩頁確認，
95-2 的體育、92-1 的高等專題都有 outline / schedule / materials）：

| 學年度 | 大綱連結率 |
|---|---|
| 96 以後 | 70%+ |
| 95 | 約 9~12% |
| 93~94 | 約 2~5% |
| 90~92 | 約 0.7~1.1% |

> ⚠️ 舊版 `plan.md` 曾寫「95-2 以前是空殼、解析結果是 `{}`」——**那是錯的**。
> 當時只抓了一頁，剛好抽到老師沒填的那種課。90-1 ~ 95-2 共 963 門有連結，
> 值得補。

### 容量沒有沿革

實驗證明 `year` / `sem` 只影響教室頁的使用率與週課表，**容量來自教室主檔**，
連不存在的學年度都回傳同一個值。所以：

- `capacity.json` 是「代碼 → 現值」，沒有歷史
- 各學期 `classrooms.json` 的 `capacity` 是**最近一次觀測值**，不是「當學期的
  座位數」。README 有寫明這個語意限制，**不要為了好看拿掉**
- `null` 有兩種（學校沒登記 vs 原本有值卻讀不到），在狀態檔裡是分開的，
  **不要合併**
- 教室代碼是穩定主鍵：51 個學期的既有資料驗過，445 個代碼零名稱衝突

---

## 3. 環境地雷

- **`python` 不是 `.venv/Scripts/python.exe`。** 系統的 `C:\Python314\python.exe`
  沒有 pytest。跑測試一律用 `.venv/Scripts/python.exe -m pytest -q`。
- **repo 的檔案是 LF。** 用 Python 寫檔時
  `.write_bytes(s.encode("utf-8").replace(b"\r\n", b"\n"))`。
- **Bash 工具的輸出中文會變亂碼**（Windows 主控台編碼）。看結構、看 ASCII，
  不要相信看到的中文字形。寫中文檔案用 Write 工具，PowerShell 工具沒這問題。
- **Bash 的 heredoc 會吃掉反斜線跳脫。** 在 `<<'PY'` 裡寫含 `\n` 的 Python
  字串常數會被弄壞。
- **`yaml.safe_load` 會把 workflow 的 `on:` 解析成布林 `True`**，不是字串
  `"on"`。驗 YAML 時用 `d[True]["workflow_dispatch"]`。
- **`gh run view --log` 只有在 run 結束後才有東西。** 看進度用
  `gh run view <id> --json jobs -q '.jobs[]|.steps[]|.name+" : "+(.conclusion//.status)'`。
- **前景 `sleep` 會被擋。** 等 workflow 用 `run_in_background` 加 `until` 迴圈。

---

## 4. 運維常識

### cron 的時間不是執行時間

GitHub Actions 只保證「不早於」，這個 repo 實測**常遲 2~4 小時**。不要用排程
時間推論「這班是不是沒跑」，要看 `runs.json` 或 `meta.json` 的 `generated_at`。
「挑離峰時段避開學校維護」在這裡做不到。

### runner 連不到學校 ≠ 學校掛了

2026-09-04、09-05 各遇過一次，簽名一樣：`ConnectTimeoutError ... timeout=30`。
同一時間從台灣打同一個 URL 是 **200、59 毫秒**——那是跨境路由或 IP 層面的
問題，不是學校維護。09-05 那次持續超過兩小時，比重試窗還長。

**處置：直接重新 dispatch，不要改 code。** 回補完全可重入，失敗的學期記在
`errors.json`，下次自動接續。判斷方式：

```bash
curl -s -o /dev/null -m 40 -w "http=%{http_code} connect=%{time_connect}s\n" \
  "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-2&year=113&sem=1"
```

從台灣通、runner 不通 → 等一等再按一次。

### 四支 workflow 共用 `concurrency: crawl`

`crawl` / `syllabus` / `backfill` / `capacity` 不會同時對學校發請求。手動
dispatch 卡到排程時它會排隊而不是併發——那是刻意的。也因此**後到的會把待命中
的那個擠掉**，排程時間才要互相錯開。

### 發布策略

- 三支例行 workflow 用 **`keep_files`** + `always()`：抓到一半失敗或 job 逾時
  被砍，也把已完成的部分推上去。推半批不會傷到任何東西。
- **`rebuild` 那一支刻意沒有 `always()`。** 它用 `force_orphan`，抓失敗時發布
  等於拿半套資料蓋掉整個分支。**不要順手幫它加上。**
- 配套是 `_write_json()` 先寫 `.tmp` 再 `os.replace()`。發布會在失敗時觸發，
  磁碟上就不能有寫到一半的 JSON。

### 每月 rebuild 是必要的

1. 壓掉 gh-pages 的歷史成長（一個月約 120 MB）
2. **清幽靈檔**：`keep_files` 永遠不刪東西，班級／教師消失後留在遠端的檔只有
   `force_orphan` 清得掉。這跟容量無關，是資料正確性。

⚠️ rebuild **只重抓當期學期**（排程觸發時沒帶 `--all-semesters`），歷史學期的
檔案是從 gh-pages 原樣 copy 回來再 force push 的——**它們永遠不會被重新產生**。

---

## 5. 資料設計上不能拿掉的東西

### 大綱用內容雜湊，不用時間戳

大綱檔帶 `content_hash`（sha256 前 16 字），**不帶任何時間戳**。抓下來算雜湊
跟舊的比，一樣就整個不寫那個檔，`keep_files` 保住遠端那份。抓取時間收進
`syllabus.json` 的 `fetched[學期][課號].at`。

沒有這個機制的話，一天兩班、每班 1,909 份大綱，即使老師一個字都沒改，git 也會
收下 1,909 個新 blob。實測有效：搬遷那次寫 1,912 份，之後幾班是 29 / 50 / 84。

配套的兩件事**拿掉就會出大事**：

1. **歷史學期一律只補沒抓過的**（`is_frozen_semester()`），
   `--syllabus-refresh-after` 對它們無效。沒有這條，補完之後每一班都會想重抓
   兩萬多頁。
2. **補完就把逐課狀態收合**成 `frozen` 裡的一筆門數。一門課的狀態約 66 bytes，
   三十幾個學期是好幾 MB，而 `syllabus.json` 每次抓大綱都會重寫——不收合等於
   把剛用雜湊省下來的 blob 換個形式吐回去。

### 進度的分子不是「狀態檔有幾筆」

`semesters[].fetched` 是**現在還有連結、而且抓過**的門數，由 `crawl_syllabi`
算好放進 `totals`。課換了老師，學校的大綱連結（綁在老師的大綱紀錄上）會消失，
但抓過的那筆狀態刻意留著——用筆數當分子會比分母大，進度顯示成 100% 以上。

> 更嚴重的是 `syllabus_done_semesters()` 用 `fetched >= with_url` 判斷學期補完
> 了沒。用筆數當分子時，幾筆過期的紀錄可以把數字墊到跟分母一樣高，於是**還沒
> 抓到的課會被判成補完**——而收合是單向的。

兩個容易漏的配套：`previous_totals` 也要沿用 `fetched`（否則換學期跑時會退回
筆數），**收合過的學期不吃這個值**（它們的 `fetched` 是定案門數）。

### 異動偵測只比兩邊都描述到的東西

`_diff_fields` 的比對走 `_differs()`：dict 只比交集的鍵，list 長度不同就是真的
變了，長度相同才逐項往下比。

理由是**索引的欄位會隨程式演進而增加**。2026-09-05 把 `language` 加進索引時，
全校 499 門非中文課會一次全部變成 `course_changed`，觸發假的 `bulk_change` 把
真正的異動收合洗掉。`time_slots` 是 `list[dict]`，它的鍵由 `TimeSlot.to_dict()`
決定——由我們自己的程式決定，所以同一種 bug 在巢狀層還會再發生一次。

**刻意不比 `enrolled` / `withdrawn`。** 加退選期間每四小時就有上千門課的人數在
動，放進來會讓事件流每次都爆掉 bulk_change。人數的時間軸走 `enrollment.json`。

### 單位頁少列班級不是停開

`_crawl_department()` 只有在 fetch 丟例外時才記錯誤。單位頁少列一個連結是
「解析成功，只是內容變少」，整條錯誤路徑不會被觸發，那個班級底下的課就直接從
資料集消失，再被異動偵測記成停開。2026-09-07 實際發生過（班級群組 2764，
10 門課）。

修法是拿上一輪的 `<學期>/classes.json` 當底，單位頁沒列到的班級照樣去抓。
**四支 workflow 的還原步驟都要有 `*/classes.json`**——`read_class_groups()`
讀不到就回空 dict、一切照舊，所以漏掉不會有任何測試失敗，假停開會直接回來。

> **這類 bug 會在事件流上留下兩筆假事件，一前一後。** 課消失時記成停開，
> 下一輪抓回來時又記成**加開**。清理各有一支腳本，判定規則不同：
>
> | | 腳本 | 判定規則 |
> |---|---|---|
> | 假加開 | `drop_phantom_additions.py` | ①大綱在它被記成加開之前就抓過了 → 先前就存在；②`--paired-with` 舊版事件流，跟一筆「已經清掉的假停開」同課號（純看已發布資料，零請求） |
> | 假停開 | `drop_phantom_removals.py` | 拿去跟學校現況對照，課還列在課表頁上 → 不是停開（每個班級群組 1 個請求） |
>
> 停開沒辦法用大綱反證——**任何一筆停開的課在事件發生前都存在過**，所以只能
> 實際去看學校現在還有沒有。也因此那支要搭配 `--at` 限縮範圍：學校真的撤掉又
> 重開的課會被誤判。
>
> ⚠️ **假加開的規則①會過期，所以清理要趁早。** `syllabus.json` 只留最後一次的
> 抓取時間，課回來之後再被抓一次大綱，時間戳就會蓋成晚於加開事件，規則①再也
> 證不出來——2026-09-08 班級 3777 那 13 筆就是這樣溜掉一輪的（隔天 05:43 又抓
> 了一次），後來靠規則②才清掉。規則②吃的是清理前的 `changes.json`，從
> gh-pages 歷史撈：`gh api "repos/OWNER/REPO/contents/changes.json?ref=<commit>"`。
>
> 清理是 gh-pages 的**單檔提交**（`data:` 開頭的 commit），不必 clone 整個分支。
> ⚠️ `rebuild_changes.py` 會把這些事件全部放回來，跑過那支之後兩支都要再跑。

### 班級課表頁壞掉也不是停開

上面那條守的是「單位頁少列班級」——班級沒被抓到。這條守的是另一半：**班級
抓到了，但學校回的那一頁根本不是課表頁。**

實際發生兩次，第二次的執行紀錄把根因寫死了：

```
2026-09-09 05:41  GET Subj.jsp?format=-4&code=3041&year=115&sem=1
                  WARNING parse_course: 課程頁找不到課程表格，回傳空清單
```

那一輪 `departments_ok=60`、`errors=0`、51 個學期的 `classes.json` 全數還原
成功——**沒有任何東西失敗**，班級也確實抓了。`parse_courses()` 把「頁面上
沒有課程表格」跟「這個班級沒有課」當成同一件事回空清單，班級 3041（互動外生三）
的 6 門課就從資料集消失，被記成 6 筆停開，下一輪抓回來又記成 6 筆加開。
2026-09-08 05:36 班級 3777（技職教育研究所）13 門是同一回事。

⚠️ `a68c5476d` 的 commit 寫著「真的被學校撤掉的班級，補抓回來會是 0 門課，
課程層級的停開判定照樣正確」——**那個假設已經被推翻**。

**分辨的依據是課程表格在不在，不是課數是不是 0。** 23 欄的表頭（`課號`
`課程名稱` `階段`…）是靜態版面的一部分，真的沒有課的班級照樣渲染得出來。
所以「整頁找不到 `課號` 表頭」只可能是頁面壞了——用它當判準，真的被學校
撤空的班級不會被誤判，也就不會卡在永遠重試（**用「0 門課」當判準會**：
被撤空的班級每一輪都是 0 門，每一輪都失敗，永遠出不來）。

修法分三層，缺一層就漏：

1. `parse_courses()` 找不到課程表格時拋 `CourseTableMissing`，不再回空清單。
   表格在、但 0 門課，照樣回空清單——那是合法的 0 門，只記一行 warning。
2. `_fetch_class_courses()` 同一輪內重抓 `CLASS_PAGE_ATTEMPTS`（3）次。
   **每次失敗都要 `fetcher.invalidate()`**——壞掉那一頁 HTTP 上是 200，
   已經被寫進 `.cache/`；不丟掉的話重抓直接命中快取，等於沒抓。
   workflow 的重試迴圈跟第一次跑在同一個 job、同一份快取，**這一步漏掉，
   那五次重試會全部讀到同一份壞 HTML，一個請求都不會真的發出去。**
3. 重抓完還是壞 → `crawl()` 拋 `IncompleteCrawl`，整個學期不輸出。守則跟
   `SiteUnavailable` 同源（「不輸出半套資料」），只是條件下沉到班級層級。
   由學期層級的容錯接住：記進 `errors.json`、`keep_files` 保住線上那份、
   回非 0 讓 workflow 重試——而快取是熱的，重試只會重抓壞掉的那一頁。

刻意等整輪跑完才拋（而不是當場），就是為了讓其他班級照抓、把快取暖起來。

### 停開要有正面證據，不能只靠「這一輪沒抓到」

上面兩條各自守一種**已知的**壞法。這一條是最後一道，不管課為什麼消失都適用。

停開判定原本是**用缺席推論**的：上一輪的索引有、這一輪的結果沒有，就記一筆
`course_removed`。缺席有兩種原因——課真的停開了，或者我們讀錯了——而它們在
輸出上一模一樣。所以 `crawl()` 跑完之後會逐筆去問學校（`_verify_removals()`），
**學校說「查無課號」才算停開**；沒證實的話整個學期不輸出（`IncompleteCrawl`）。

證據來自教學大綱頁。2026-09-09 實抓四頁確認：

| 網址 | 結果 |
|---|---|
| `ShowSyllabus.jsp?snum=364585` | `錯誤訊息 : 查無課號 (364585) 的開課資料`（真停開） |
| `ShowSyllabus.jsp?snum=362717` | 同上（第二個樣本） |
| `ShowSyllabus.jsp?snum=362160` | 課程基本資料列出 `115-1 362160 互動設計創作(一)` |

兩個**實測過**、會影響寫法的性質：

- **`code` 參數不影響課程基本資料。** `snum=364585&code=11494` 與
  `snum=364585&code=11969`（別門課的 code）回傳的頁面**逐字相同**。所以查存在
  只需要課號——`index.json` 裡就有，不必為此多存 `syllabus_url`。
- **判準是課程基本資料那一列，不是有沒有大綱內容。** `code` 只決定要不要顯示
  大綱：不帶 code 時課程基本資料照樣完整渲染，下面卻變成「尚未登錄」，跟停開的
  頁面長得一樣。**拿大綱內容當判準會把還在的課判成停開。**

之所以能拿它當反證：課表頁（`format=-4`）與大綱頁讀的是**同一份開課資料**。
「大綱頁查得到、課表頁沒列到」在邏輯上互相矛盾，那不是停開，是我們讀錯了。

三件不能拿掉的配套：

1. **判不出來 ≠ 停開。** `course_exists()` 回 `None`（版面不認得、課號對不上、
   請求失敗）一律當成沒有證據，跟「課還在」同樣處理。這個函式存在的理由就是
   要有證據，判不出來時退回猜測等於白做。
2. **`--dept` 的局部抓取不給 `known_courses`。** 那一輪只抓幾個系所，拿全校的
   索引來比，沒抓的兩千多門課全部都是「消失了」。跟 `_write_changes()` 對局部
   抓取略過變更紀錄是同一個理由。
3. **`REMOVAL_VERIFY_LIMIT`（30）是抽樣門檻，不是拒絕門檻。** 消失的課超過它就
   只抽前 30 門問。**不可以改成「超過就一律不發布」**——真的大批停開會發生
   （README 記過一次 265 門的 `bulk_change`），那樣會**永久卡死**：那些課不會
   再回來，每一輪都卡在同一個地方。抽樣兩邊都顧得到：抓取缺口一定是整批
   「課還在」，抽幾門就現形；真的大批停開是整批「查無課號」，抽樣會過。

成本平常是**零個請求**：真停開很少，沒有候選就不問。

> ⚠️ 這條讓「假停開」這個 bug 家族整族失效，但**上面兩條仍然要留著**。它們在
> 更早的階段就攔下來（不必多花請求、也保住那個班級的課），而且拿回來的是真的
> 資料；這條只證明「不該記成停開」，救不回那些課的內容。

### 4xx 要重設斷路器，而且一樣要 sleep

`ClientError` 原本直接穿過 `fetch()`，`consecutive_failures = 0` 和
`time.sleep()` 都沒跑到。「連不上、連不上、404、連不上」會累積成
`UNAVAILABLE_AFTER` 讓整輪中止，而中間那個 404 正好證明學校活著。

⚠️ `test_4xx_does_not_count_towards_the_circuit` **驗不到這個**——計數本來就從
0 開始，那個測試是自動通過的。真正的情境要用交錯的失敗去測。

---

## 6. 已經決定的事（不要自作主張推翻）

### v4 的索引瘦身：評估過，決定不做（2026-09-07）

提案是把 `enrolled` / `withdrawn` 從索引拿掉、只留在人數快照裡。**否決**，
三個理由：

1. **收益打錯地方。** 省的是每輪約 2.79 MB 的 git 歷史成長，而那個數字每月的
   `force_orphan` 重建本來就整個壓掉了。真正往 Pages 的 1 GB 上限爬的是發布出去
   的那棵樹，而它由大綱檔主導（約 79,000 份 × 4.7 KB ≈ 370 MB）。
2. **影響無法評估。** JSON 端點沒有任何遙測——GA 只跑在四個 HTML 頁上，JSON
   裡沒有能執行 JS 的地方，Pages 也不提供逐檔存取記錄。而 JavaScript 讀到
   `undefined` 會**靜靜地錯**。
3. **會留下兩種形狀。** rebuild 不重抓歷史學期（見第 4 節），線上會永久並存
   「新學期沒有人數、舊學期有」。

真要省容量，非破壞性的槓桿效果都比它大：rebuild 改每兩週、crawl 從 4 小時改
6 小時、大綱檔瘦身。**要量到 JSON 的使用只有一條路：在請求路徑上放東西**
（網域 + 反向代理）。那是做任何破壞性改動的前提。

`schema_version` 維持 **3**。README 承諾「新增欄位、新增端點不會升版」，
教室容量整套就是這樣加進去而沒升版的。

### 還沒處理的小事

- 教師 email 是整批發布出去的（`teacher_email` 欄位），沒有人問過該不該做
- 人數快照的 `date` 是台北時區、`at` 是 UTC，跨日邊界上兩者會對不起來
- 4xx 目前不計入 `failed_url_count`（語意是「重試到底仍然連不上」），
  所以 `runs.json` 看不到 4xx 的次數

---

## 7. 現況與待驗證

> 這一節會過期。數字對不上時以線上端點為準，不要相信這裡。

大綱回補：**96-1 ~ 114-2 共 38 個學期已收合**，115-1 是當期。**90-1 ~ 95-2
共 12 個學期還沒補**——排程會自己接續（每 6 小時一批 6 個學期，範圍 `90-113`）。
補完 90-1 之後就會空轉，那是正常的。

```bash
curl -s https://tntrock.github.io/ntut-course-crawler/syllabus.json | python -c "
import sys, json
f = set(json.load(sys.stdin).get('frozen', {}))
want = [f'{y}-{s}' for y in range(114, 89, -1) for s in (2, 1)]
print('已完成:', len(f), '個學期')
print('還沒補:', [x for x in want if x not in f])"
```

還沒被實際觸發過的路徑：

- **`capacity.yml` 的排程那一半**（cron `0 2 1 * *`）與**每月整包重建**
  （`crawl.yml` 的 `0 6 1 * *`），都要等 10/1 才驗得到。手動觸發那一半都驗過了。
- **`attempts` 欄位**還沒有任何一筆 > 1 的實際資料。
  ⚠️ `attempt`（GitHub 的 re-run）和 `attempts`（我們自己的重試迴圈）是兩個
  不同的東西，不要搞混。

---

## 8. 驗證清單

改完東西之後依序做：

```bash
# 1. 測試
.venv/Scripts/python.exe -m pytest -q

# 2. workflow YAML 語法
python -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{f}.yml', encoding='utf-8')) for f in ['crawl','syllabus','backfill','capacity','test']]; print('ok')"

# 3. 線上端點還活著
for f in meta.json index.json syllabus.json runs.json errors.json changes.json enrollment.json capacity.json; do
  curl -s -o /dev/null -w "$f %{http_code} %{size_download}\n" \
    "https://tntrock.github.io/ntut-course-crawler/$f"
done

# 4. 最近幾次跑得如何
curl -s https://tntrock.github.io/ntut-course-crawler/runs.json | python -c "
import sys, json
for r in json.load(sys.stdin)['runs'][:10]:
    print(r['at'], r['workflow'], r['status'], r.get('failed_semesters') or '')"
```

工作流程是 **branch → PR → merge**（不直接推 main），`test` workflow 會在 PR
上跑。commit 訊息結尾要帶：

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
