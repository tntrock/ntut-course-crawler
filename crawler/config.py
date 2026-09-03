"""全域設定常數。

這裡集中所有「換一個部署環境就要改」的值,其他模組不要自己寫死。
"""

from __future__ import annotations

import os

from . import __version__

#: 課程系統的 base URL,結尾必須有斜線(urljoin 用)
BASE_URL = "https://aps.ntut.edu.tw/course/tw/"

#: 對外 API 的 schema 版本。輸出的每個 JSON 頂層都會帶這個值。
#: 一旦有人使用,格式變更就是 breaking change,務必同步升版。
SCHEMA_VERSION = 1

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
TIMEOUT = (10, 30)

#: 本地 HTTP 快取目錄
CACHE_DIR = ".cache"
