"""Phase 3 偵察:確認進修部課程是否在同一棵 format=-2 樹裡(plan.md §7-1)。

背景:總覽頁的 60 個單位裡沒有「進修部」這種單位,而資工系底下只有
資工一~四 + 資工所,看不到夜間班。無法從離線 fixture 判斷究竟是
「進修部不在這棵樹上」還是「只有部分系所有夜間班」。

做法:挑一個已知有進修部的系所(機械系 code=30),抓它的 format=-3
班級列表看有沒有夜間班。這是刻意的例外請求,經使用者核准,
依 plan.md §5.3「一次只抓一頁並說明理由」。

順便驗收 Phase 1:真的連得上、SSL 過得去、中文不亂碼。

用法:
    python -m scripts.recon3 [系所代碼 ...]
"""

from __future__ import annotations

import logging
import sys

from crawler.http import TRUSTSTORE_ACTIVE, Fetcher
from crawler.parse_dept import parse_class_groups

YEAR = 115
SEM = 1
DEFAULT_CODES = ["30"]  # 機械系


def main(codes: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"truststore active: {TRUSTSTORE_ACTIVE}")

    fetcher = Fetcher(delay=2.0)  # 偵察時放慢到 2 秒
    for code in codes:
        html = fetcher.fetch(
            "Subj.jsp", params={"format": -3, "year": YEAR, "sem": SEM, "code": code}
        )
        title = html.split("<H2>", 1)[-1].split("</H2>", 1)[0].strip()
        print(f"\n=== code={code} :: {title} ===")
        for group in parse_class_groups(html, code):
            print(f"  {group.id:>6}  {group.name}")

    print(
        f"\n請求 {fetcher.request_count} 次 / 快取命中 {fetcher.cache_hit_count} 次"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or DEFAULT_CODES))
