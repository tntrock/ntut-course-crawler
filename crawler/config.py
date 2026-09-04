"""全域設定常數。

這裡集中所有「換一個部署環境就要改」的值,其他模組不要自己寫死。
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone

from . import __version__

#: 台灣時區。學校頁面顯示的時間、以及「今天」的界線都用它。
#: 台灣沒有日光節約時間,固定 +8 就是正確的,不必依賴 tzdata。
TAIPEI = timezone(timedelta(hours=8))

#: 課程系統的 base URL,結尾必須有斜線(urljoin 用)
BASE_URL = "https://aps.ntut.edu.tw/course/tw/"

#: 對外 API 的 schema 版本。輸出的每個 JSON 頂層都會帶這個值。
#: 一旦有人使用,格式變更就是 breaking change,務必同步升版。
#:
#: v2(2026-09-04):為了容納 90 學年度起的歷史資料而改了兩件事 ——
#:   1. `generated_at` 只留在 meta.json / errors.json。原本每個檔都有,
#:      導致每次跑完所有檔案內容都變,發布時等於整包重推。
#:   2. 頂層 index.json 只涵蓋最新 INDEX_SEMESTERS 個學期,更舊的查
#:      `{semester}/index.json`。50 個學期全塞進去會膨脹到數十 MB。
SCHEMA_VERSION = 2

#: 頂層 index.json 涵蓋幾個最新的學期。歷史學期一律走 `{semester}/index.json`。
INDEX_SEMESTERS = 2

#: GitHub repo(`owner/repo`)。
#: - 在 GitHub Actions 裡 `GITHUB_REPOSITORY` 是內建環境變數,會自動帶入正確值。
#: - 本機開發時可 `export GITHUB_REPOSITORY=yourname/ntut-course-crawler` 覆寫。
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY") or "UNSET/ntut-course-crawler"

#: User-Agent 必須有辨識度並附聯絡方式(plan.md §1.4 硬性規定)
USER_AGENT = f"ntut-course-crawler/{__version__} (+https://github.com/{GITHUB_REPOSITORY})"

#: 每次請求後的固定延遲(秒)。可由環境變數 CRAWL_DELAY 或 CLI --delay 覆寫。
DEFAULT_DELAY = 1.0

#: 延遲下限。**不得為了加快速度而調低**(plan.md §1.4 / §5.2)。
MIN_DELAY = 0.5

#: (connect timeout, read timeout)
#:
#: connect 給得比 read 寬鬆。GitHub runner 在美國,跨太平洋連學校主機,
#: 2026-09-04 08:33 UTC 那次排程就是四次 connect 全部逾時、整個 run 掛掉 ——
#: 同一時間從台灣連是通的(0.05 秒就握上手)。10 秒對正常連線綽綽有餘,
#: 但跨境路由抖一下就不夠,而多等幾秒的代價遠小於整批重跑。
TIMEOUT = (30, 60)

#: 重試:一輪打幾次。輪內用指數退避(2 / 4 / 8 秒)。
RETRY_ATTEMPTS_PER_ROUND = 4

#: 重試:總共打幾輪。
RETRY_ROUNDS = 2

#: 輪與輪之間的等待(秒)。
#:
#: 一輪四次、間隔幾秒全部落空,通常不是這個網址有問題,而是對方當下整個
#: 不可用(維護、防火牆、跨境線路中斷)。這種狀況下密集重試既沒用也不禮貌,
#: 不如整個停下來等 3 分鐘 —— 實測的中斷多半在這個量級內就恢復了。
RETRY_ROUND_PAUSE = 180.0

#: 連續幾個網址「重試到底仍然失敗」就判定站台整體不可用。
#:
#: 判定成立後,同一次執行剩下的請求一律直接失敗、不再重試。沒有這道閘,
#: 上面那套「重試很久」的設定會讓一次全站中斷變成每個網址都耗掉數分鐘,
#: 幾百個網址就是好幾個小時 —— 對學校是噪音,對我們是浪費 runner。
UNAVAILABLE_AFTER = 3

#: 本地 HTTP 快取目錄
CACHE_DIR = ".cache"
