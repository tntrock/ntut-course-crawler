# 交接文件

寫於 2026-09-06。給接手這個 repo 的下一個人 / agent。

這份文件只寫**「讀 code 讀不出來」**的東西：進行中的工作、待驗證的假設、
踩過的坑、以及刻意沒做的決定。專案本身怎麼運作請讀 `README.md`
（很長但很完整），抓取禮儀的硬性規定請讀 `plan.md`。

---

## 1. 先做這件事：確認回補跑完了沒

**這是交接時唯一進行中的工作。**

教學大綱的歷史回補正在分批進行，目標是補到 **110-1**。交接當下的狀態：

| 學期 | 大綱 |
|---|---|
| 115-1 | 當期，每天兩班全跑 |
| 114-2、114-1、113-2 | ✅ 已補完並收合 |
| 113-1、112-2、112-1 | 🔄 [run 33977412961](https://github.com/tntrock/ntut-course-crawler/actions/runs/33977412961) 進行中 |
| 111-2、111-1、110-2、110-1 | ⬜ 還沒補 |

先查現況：

```bash
curl -s https://tntrock.github.io/ntut-course-crawler/syllabus.json | python -c "
import sys, json
d = json.load(sys.stdin)
done = set(d.get('frozen', {})) | {'115-1'}
want = [f'{y}-{s}' for y in range(114, 109, -1) for s in (2, 1)]
print('已完成:', [x for x in want if x in done])
print('還沒補:', [x for x in want if x not in done])"
```

還有沒補完的就再按一次，**一次三個學期，會自動接續**：

```bash
gh workflow run backfill.yml -f years=110-113 -f with_syllabus=true
```

一批約 2.6 小時（一個學期 355 頁課表 + 約 2,200 頁大綱，1.2 秒/頁）。
補完的學期會登記進 `syllabus.json` 的 `frozen`，之後永遠跳過，所以重複按
不會重做。**補完 110-1 之後這件事就結束了，不要再往前補**——理由見第 5 節。

---

## 2. 這個 session 改了什麼（4 個 commit）

```
f754d78  fix(runlog): 讓紀錄誠實一點,並補上重試次數
852d7f5  feat: 每次執行都留下紀錄(runs.json),並拿掉學期間的暫停
334c2c8  feat: 抓到一半也把成果推上去,並讓寫檔變成原子的
7772cbb  feat(syllabus): 內容雜湊取代 fetched_at,並支援回補歷史學期的大綱
```

### schema v2 → v3（`7772cbb`）

**這是 breaking change**，`SCHEMA_VERSION` 已升到 3，README 有 v3 章節。

大綱檔原本帶 `fetched_at`，每次抓取都變，於是一天兩班、每班 1,909 份大綱，
即使老師一個字都沒改，git 也會收下 1,909 個新 blob。改成：

- 大綱檔帶 `content_hash`（sha256 前 16 字），不帶任何時間戳
- 抓下來算雜湊跟舊的比，**一樣就整個不寫那個檔**，`keep_files` 保住遠端那份
- 抓取時間收進 `syllabus.json` 的 `fetched[學期][課號].at`

配套的兩件事**不能拿掉**，拿掉就會出大事：

1. **歷史學期一律只補沒抓過的**（`is_frozen_semester()`），
   `--syllabus-refresh-after` 對它們無效。沒有這條，補完之後每一班都會想
   重抓兩萬多頁。
2. **補完就把逐課狀態收合**成 `frozen` 裡的一筆門數。一門課的狀態約 66 bytes，
   11 個學期兩萬多筆是 1.6 MB，而 `syllabus.json` 每次抓大綱都會重寫——
   不收合等於把剛用雜湊省下來的 blob 換個形式吐回去。

### 失敗也發布 + 原子寫檔（`334c2c8`）

三支 workflow 的 `Publish to gh-pages` 加了 `always()`：抓到一半失敗、
或 job 逾時被砍，也把已完成的部分推上去。它們都是 `keep_files`，
推半批不會傷到任何東西。

> **`rebuild` 那一支刻意沒有 `always()`。** 它用 `force_orphan`，抓失敗時
> 發布等於拿半套資料蓋掉整個分支。**不要順手幫它加上。**

配套是 `_write_json()` 改成先寫 `.tmp` 再 `os.replace()`。發布現在會在失敗時
觸發，磁碟上就不能有寫到一半的 JSON——`index.json` 有 1.9 MB，job 逾時是
直接砍行程的。

### 執行紀錄 `runs.json`（`852d7f5` + `f754d78`）

使用者接下來要做一個**狀態頁**，需要「爬蟲現在怎麼樣」這件事是可以 `fetch()` 的。
Actions 的 log 回答不了（要登入或帶 token，只留 90 天），所以多了根目錄的
`runs.json`，保留最近 120 筆。

- 由 workflow 的 `Record this run` 步驟寫，`always()`，**成功／失敗／逾時被砍
  都會留下一筆**
- 狀態取自 `job.status` 而不是爬蟲的回傳值——最需要紀錄的那幾次爬蟲根本沒機會寫
- 爬蟲的細節走 `--run-summary` 側寫檔，**每抓完一個學期就重寫一次**
- 側寫檔不存在時記 `detail: false`，**不填 0**：「跑了但什麼都沒抓到」跟
  「根本沒跑到」是兩回事

`f754d78` 是第一次實戰後的修正，欄位名稱改過，注意不要用到舊的：

| 舊 | 新 | 為什麼 |
|---|---|---|
| `requests` | `requests_ok` + `failed_urls` | 原本 `0` 看起來像沒跑，其實是跑了但一個都沒成功 |
| `started_at` | `attempt_started_at` | 那是最後一次嘗試的時間，不是整個 run 的 |
| （無） | `attempts` | workflow 內部整批重試跑了幾次 |

⚠️ `attempt`（GitHub 的 re-run 按鈕）和 `attempts`（我們自己的重試迴圈）
是兩個不同的東西，不要搞混。

---

## 3. 待驗證：下一班大綱是關鍵

**這是目前最重要的未驗證假設。**

2026-09-05 17:01 UTC 那班大綱是 v3 上線後的第一次，紀錄是：

```json
{"syllabus_fetched": 1912, "syllabus_written": 1912}
```

**全部重寫是預期的**——舊狀態沒有雜湊可比，那是一次性搬遷，README 有寫。

真正要驗的是**再下一班**（台灣 09:00 / 21:00，但 cron 常遲 2~4 小時）。
如果雜湊機制正確：

```
syllabus_fetched ≈ 1912   ← 還是全部重抓,這是刻意的
syllabus_written ≈ 0~50   ← 只有老師真的改過的才重寫
```

`syllabus_written` 如果又是 1912，代表雜湊沒生效，要查 `syllabus_content_hash()`
或狀態的還原路徑。查法：

```bash
curl -s https://tntrock.github.io/ntut-course-crawler/runs.json | python -c "
import sys, json
for r in json.load(sys.stdin)['runs']:
    if r['workflow'] == 'syllabus' and r.get('detail'):
        for s in r['semesters']:
            print(r['at'], s['semester'], s['syllabus_fetched'], '抓 /',
                  s['syllabus_written'], '寫')"
```

其他還沒被實際觸發過的路徑：

- **每月 1 號的整包重建**：`REBUILD` 表達式的 `github.event.schedule == '0 6 1 * *'`
  那一半**要等 10/1 才驗得到**（手動觸發走的是 `inputs.rebuild` 那一半，已驗過）。
  萬一沒對上，結果只是那班照常發布、沒壓歷史，不會弄壞東西。
- **`always()` 發布**在 2026-09-05 那次失敗已驗過（`Publish to gh-pages : success`）。
- **`attempts` 欄位**還沒有任何一筆實際資料（`f754d78` 之後還沒有 run 重試過）。

---

## 4. 環境地雷（會浪費你時間的那些）

- **`python` 不是 `.venv/Scripts/python.exe`。** 系統的 `C:\Python314\python.exe`
  沒有 pytest。跑測試一律用 `.venv/Scripts/python.exe -m pytest -q`。
- **Bash 的 heredoc 會吃掉反斜線跳脫。** 在 `<<'PY'` 裡寫 Python 字串常數
  含 `\n` 會被弄壞（這個 session 踩過兩次）。要嘛用 `chr(92)` 組出來，
  要嘛改用 Write 工具寫檔再套用。
- **Bash 工具的輸出中文會變亂碼**（Windows 主控台編碼）。看結構、看 ASCII，
  不要相信看到的中文字形。PowerShell 工具沒這問題。
- **repo 的檔案是 LF。** 用 Python 寫檔時記得
  `.write_bytes(s.encode("utf-8").replace(b"\r\n", b"\n"))`。
- **`gh run view --log` 只有在 run 結束後才有東西**，跑到一半查會是空的。
  要看進度用 `gh run view <id> --json jobs -q '.jobs[]|.steps[]|.name+" : "+(.conclusion//.status)'`。
- **前景 `sleep` 會被擋。** 等 workflow 要用 `run_in_background` 加 `until` 迴圈。
- **`yaml.safe_load` 會把 workflow 的 `on:` 解析成布林 `True`**，不是字串 `"on"`。
  驗 YAML 時用 `d[True]["workflow_dispatch"]`。

---

## 5. 已知但刻意沒做的事

**這些不要自作主張去做，都是有理由留著的。**

### 大綱不要往 96-1 補

學校的資料到 96-1 都還在（每學期兩千多門有連結），但：

- **95-2 以前是空殼**：連結率塌到 12% 以下，實抓一頁確認過 HTTP 200 但內容
  欄位全空，解析結果是 `{}`。往前補沒有東西可補。
- **96-1 到 111-2 補下去會爆容量**：40 個學期、八萬多份大綱，估計讓 gh-pages
  從 44 MB 長到 300 MB 上下。補到 110-1（11 個學期、約 24,000 份）估計
  100 MB 左右，那是使用者選的停損點。

要往前補的話改 `-f years=` 就行，但**先跟使用者確認容量**。

### 容量的下一個槓桿（v4 等級，別自己動）

現在每天的 gh-pages 增量由 `index.json`（1.87 MB）和 `115-1/index.json`
（0.92 MB）每 4 小時重寫主導——它們會變是因為 `enrolled` 真的在動。
2026-09-05 實測一次 `crawl` 發布 = 65 個新物件、未壓縮 3.67 MB。

真要再砍一半，就是把 `enrolled` / `withdrawn` 從 `index.json` 拿掉、
只留在人數快照裡。那樣索引會變成幾乎靜態。**但那是破壞性改動**，
會影響任何用索引篩人數的使用端，要升 v4 並跟使用者確認。

### 每月 rebuild 還是必要的

即使雜湊把大綱那半邊的增量砍到接近零，rebuild 仍有兩個用途：

1. 剩下那一半（索引檔）砍不掉，一個月約 120 MB
2. **清幽靈檔**：`keep_files` 永遠不刪東西，班級／教師消失後留在遠端的檔
   只有 `force_orphan` 清得掉。這跟容量無關，是資料正確性。

### 其他還沒處理的小事

- `_diff_fields` 沒有防 `time_slots` 巢狀結構長大——學校加欄位時可能誤判成異動
- 4xx 不會重設 `consecutive_failures`，理論上可能讓斷路器誤跳
- 教師 email 是整批發布出去的（`teacher_email` 欄位），沒有人問過這件事該不該做
- 人數快照的 `date` 是台北時區、`at` 是 UTC，跨日的邊界上兩者會對不起來
- 回補會在 `changes.json` 留下 `baseline` 事件（每個超出索引涵蓋範圍的學期一筆）。
  已確認**不會**灌進幾千筆 `course_added`，但補完 10 個學期就是 10 筆 baseline。

---

## 6. 運維常識（不知道會誤判）

### cron 的時間不是執行時間

GitHub Actions 只保證「不早於」。這個 repo 實測**常遲 2~4 小時**。
不要用排程時間去推論「這班是不是沒跑」，要看 `runs.json` 或 `meta.json`
的 `generated_at`。「挑離峰時段避開學校維護」在這裡是做不到的事。

### runner 連不到學校 ≠ 學校掛了

**這個 session 兩天內遇到兩次**（9/4、9/5），簽名一模一樣：

```
ConnectTimeoutError ... connect timeout=30
```

每次從台灣打同一個 URL 都是 **200、59 毫秒**。所以那是跨境路由或 IP 層面的
問題，不是學校維護。9/5 那次持續超過兩小時，比 3×30 分的重試窗還長。

**處置方式：直接重新 dispatch，不要改 code。** 回補完全可重入，
失敗的學期記在 `errors.json`，下次會自動接續。判斷方式：

```bash
curl -s -o /dev/null -m 40 -w "http=%{http_code} connect=%{time_connect}s\n" \
  "https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-2&year=113&sem=1"
```

從台灣通、runner 不通 → 等一等再按一次就好。

### 三支 workflow 共用 `concurrency: crawl`

`crawl`、`syllabus`、`backfill` 不會同時對學校發請求。所以手動 dispatch
回補時，如果剛好卡到 4 小時排程，它會排隊而不是併發——那是刻意的。

---

## 7. 絕對不要做的事（`plan.md` 硬性規定）

這幾條在 `plan.md` 裡是明文寫死的，**不接受為了「快一點」而放寬**：

- **單執行緒。** 不使用 `threading` / `asyncio` / `multiprocessing` 平行抓取。
- **每次請求後 sleep**，預設 1.0 秒，**下限 0.5 秒**，不得調低。
- **開發期一律用 fixture 或快取**，不要為了驗證反覆打學校伺服器。
  真的需要新樣本時一次只抓一頁，並說明理由。
  （這個 session 只實抓過三頁：兩頁驗舊學期大綱是否存在、一頁驗學校可達性。）
- **不要用 `verify=False` 關掉憑證驗證。** 學校憑證缺 SKI，正確解法是
  `truststore`（`http.py` 已經處理），那是「解掉」不是「蓋掉」。

測試有一道安全網：`tests/conftest.py` 的 `no_real_network` fixture 會讓任何
發出真實 HTTP 請求的測試直接失敗。不要拆掉它。

---

## 8. 驗證清單

改完東西之後，這幾件依序做：

```bash
# 1. 測試(目前 299 個)
.venv/Scripts/python.exe -m pytest -q

# 2. workflow YAML 語法
python -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{f}.yml', encoding='utf-8')) for f in ['crawl','syllabus','backfill','test']]; print('ok')"

# 3. 線上端點還活著
for f in meta.json index.json syllabus.json runs.json errors.json changes.json enrollment.json; do
  curl -s -o /dev/null -w "$f %{http_code} %{size_download}\n" \
    "https://tntrock.github.io/ntut-course-crawler/$f"
done

# 4. 最近幾次跑得如何
curl -s https://tntrock.github.io/ntut-course-crawler/runs.json | python -c "
import sys, json
for r in json.load(sys.stdin)['runs'][:10]:
    print(r['at'], r['workflow'], r['status'], r.get('failed_semesters') or '')"
```

commit 訊息結尾要帶：

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

---

## 9. 使用者接下來想做的事

**做一個狀態頁**，呈現爬蟲在抓資料與整理資料的狀態。需要的資料都已經備好了，
全部是 CORS 開放的靜態 JSON，前端直接 `fetch()`：

| 檔案 | 回答什麼問題 |
|---|---|
| `runs.json` | 最近 120 次跑得如何（含失敗與逾時） |
| `meta.json` 的 `semesters[].generated_at` | 每個學期的資料多新 |
| `errors.json` | 現在有哪些單位／學期抓不到 |
| `syllabus.json` | 大綱補到哪了（`semesters` 是進度，`frozen` 是已完成） |

`web/index.html` 是目前發布到 gh-pages 根目錄的說明頁，由 workflow 複製過去。
狀態頁如果要取代它，記得三支 workflow 的 `Add landing page` 步驟都要跟著改。
