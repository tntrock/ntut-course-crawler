"""從 `changes.json` 移除「抓取缺口造成的假停開」。

**一次性的維護腳本,不參與正式流程。**

為什麼會需要它
--------------
班級的課表頁偶爾會回一頁「解析成功但一門課都沒有」的內容。`_crawl_department()`
的迴圈遇到 0 筆時什麼都不做 —— 不記錯誤、不警告 —— 那個班級底下的課就安靜地
從資料集消失,再被異動偵測記成一批停開。

2026-09-08 05:36 實際發生:班級群組 3777(技職教育研究所)的 13 門課同時
「停開」,實抓學校那一頁確認 13 門全都還在。

這跟 `drop_phantom_additions.py` 處理的是同一個 bug 的另一半 —— 課消失時記成
停開,下一輪抓回來時記成加開。

判定規則
--------
**把停開事件拿去跟學校的現況對照:課還列在那個班級的課表頁上,就不是停開。**

加開那一半可以用「大綱早就抓過」反證,停開不行 —— 任何一筆停開的課在事件發生
前都存在過。所以這裡只能實際去看學校現在還有沒有這門課。

每個受影響的**班級群組**只發一次請求(一頁列出該班級的全部課程),不是每門課
一次。2026-09-08 那次是 1 個請求。

⚠️ 學校真的撤掉又重開的課會被誤判成假停開。所以**一律搭配 `--at` 把範圍限縮
到你確認過的那一批事件**,不要對整個事件流無差別跑。

用法
----
    python scripts/drop_phantom_removals.py --at 2026-09-08T05:36:54Z --dry-run
    python scripts/drop_phantom_removals.py --at 2026-09-08T05:36:54Z --out changes.json

產出的檔案覆蓋 gh-pages 根目錄的 `changes.json` 即可(單檔提交,不必 clone
整個分支)。**一律用 `--out` 而不是 shell 重導向** —— Windows 主控台是 cp950,
`>` 會把中文寫壞。

注意
----
`rebuild_changes.py` 會從 gh-pages 歷史重建整條事件流,**把這些事件放回來**。
跑過那支之後要接著跑這支與 `drop_phantom_additions.py`。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.http import Fetcher  # noqa: E402
from crawler.parse_course import parse_courses  # noqa: E402

BASE = "https://tntrock.github.io/ntut-course-crawler"


def load(source: str | None, name: str) -> dict:
    if source:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return json.load(resp)


def targets(events: list[dict], at: str | None) -> list[dict]:
    return [
        e
        for e in events
        if e.get("type") == "course_removed"
        and (at is None or e.get("at") == at)
        and e.get("class_ids")
    ]


def live_course_ids(fetcher: Fetcher, semester: str, class_id: str) -> set[str]:
    """那個班級現在的課表頁上有哪些課號。"""
    year, _, sem = semester.partition("-")
    html = fetcher.fetch(
        "Subj.jsp",
        params={"format": -4, "year": year, "sem": sem, "code": class_id},
    )
    return {c.id for c in parse_courses(html)}


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changes", help="本機的 changes.json(預設讀線上)")
    parser.add_argument(
        "--at", help="只檢查這個時間戳的停開事件(強烈建議指定,見 docstring)"
    )
    parser.add_argument("--delay", type=float, default=1.5, help="每次請求後的延遲")
    parser.add_argument(
        "--dry-run", action="store_true", help="只列出會刪掉什麼,不輸出檔案"
    )
    parser.add_argument("--out", help="輸出路徑(UTF-8 + LF)")
    args = parser.parse_args()

    if not args.dry_run and not args.out:
        parser.error("要嘛 --dry-run,要嘛用 --out 指定輸出路徑")

    changes = load(args.changes, "changes.json")
    events = changes.get("events") or []
    candidates = targets(events, args.at)
    if not candidates:
        print("沒有符合條件的停開事件", file=sys.stderr)
        return 1

    # 一個班級群組只查一次:那一頁就列出它底下的全部課程
    groups = sorted({(e["semester"], e["class_ids"][0]) for e in candidates})
    print(f"要檢查 {len(candidates)} 筆停開,涉及 {len(groups)} 個班級群組", file=sys.stderr)

    fetcher = Fetcher(delay=args.delay, use_cache=False)
    live: dict[tuple[str, str], set[str]] = {}
    for semester, class_id in groups:
        live[(semester, class_id)] = live_course_ids(fetcher, semester, class_id)
        print(
            f"  {semester} 班級 {class_id}:學校現在列出 "
            f"{len(live[(semester, class_id)])} 門",
            file=sys.stderr,
        )

    phantom = [
        e
        for e in candidates
        if e["id"] in live.get((e["semester"], e["class_ids"][0]), set())
    ]

    print(f"\n判定為假停開 {len(phantom)} / {len(candidates)} 筆:", file=sys.stderr)
    for e in phantom:
        print(f"  {e['at']}  {e['id']}  {e.get('name')}  ← 還列在課表上", file=sys.stderr)
    for e in candidates:
        if e not in phantom:
            print(f"  (保留) {e['at']}  {e['id']}  {e.get('name')}", file=sys.stderr)

    if args.dry_run:
        return 0
    if not phantom:
        print("沒有要移除的事件,不輸出", file=sys.stderr)
        return 1

    drop = {id(e) for e in phantom}
    changes["events"] = [e for e in events if id(e) not in drop]
    text = json.dumps(changes, ensure_ascii=False, separators=(",", ":")) + "\n"
    # 明確 UTF-8 + LF。這個 repo 的檔案是 LF,而 Windows 的預設編碼是 cp950。
    Path(args.out).write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))
    print(f"移除 {len(phantom)} 筆假停開 → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
