"""從 `changes.json` 移除「抓取缺口造成的假加開」。

**一次性的維護腳本,不參與正式流程。**

為什麼會需要它
--------------
單位頁偶爾會少列班級,那個班級底下的課就整批從資料集消失(根因與修法見
`reference.md`「單位頁少列班級不是停開」)。下一輪抓回來時,異動偵測只看得到
「上一輪沒有、這一輪有」,於是記成一批 `course_added` —— 但那些課從來沒有
被開過,只是我們自己漏抓了一輪。

2026-09-07 實際發生兩次,一次一半:

- 09:47 那班少列班級群組 2764,10 門課被記成停開
  → 前一個 session 已手動移除(gh-pages commit `data: 移除 10 筆 bug 造成的假停開`)
- 15:00 那班把它們抓回來,同樣 10 門被記成**加開**
  → 這支腳本處理的就是這一半

判定規則
--------
兩條,符合任一條就是假加開。

**規則一:一門課的教學大綱在它被記成加開之前就抓過了 → 它先前就存在。**

大綱是照著當時的課表逐課抓的,所以 `syllabus.json` 裡有一筆抓取時間,就證明
那個時間點這門課在課表上。真正新開的課不可能在出現之前就被抓過大綱。

⚠️ **`syllabus.json` 只留最後一次的抓取時間。** 課回來之後又被抓過一次大綱,
那個時間戳就會蓋掉舊的、變成晚於加開事件,這條規則就再也證不出來了 ——
2026-09-08 班級 3777 那 13 筆實際上就是這樣溜掉的(隔天 05:43 又抓了一次)。
所以**清理要趁早**,或者改用規則二。

**規則二:它跟一筆「已經被判定為假、而且已經刪掉」的停開對得起來。**

`--paired-with` 吃一份**清理前**的 `changes.json`(從 gh-pages 的歷史撈,
例如 `gh api "repos/OWNER/REPO/contents/changes.json?ref=<清理前的 commit>"`)。
舊檔裡有、新檔裡沒有的停開 = 上一次已經查證過學校、確認是假的那一批;
同課號的加開就是它回來時留下的另一半,一樣是假的。

兩條規則都不會誤刪真正的加開。共同的代價是漏掉「沒有大綱連結、也沒有配對
停開紀錄」的課 —— 那種只能靠人工判斷,腳本不猜。

用法
----
    python scripts/drop_phantom_additions.py --dry-run       # 先看會刪掉什麼
    python scripts/drop_phantom_additions.py --out changes.json
    python scripts/drop_phantom_additions.py \
        --paired-with before.json --out changes.json

預設從線上端點讀,也可以用 --changes / --syllabus 指定本機檔案。產出的檔案
覆蓋 gh-pages 根目錄的 `changes.json` 即可(單檔提交,不必 clone 整個分支)。

**一律用 `--out` 而不是 shell 重導向。** Windows 的主控台編碼是 cp950,
`> changes.json` 會把中文寫壞;`--out` 明確用 UTF-8 + LF 寫檔。

注意
----
`rebuild_changes.py` 是從 gh-pages 的歷史重跑整條事件流,**它會把這些事件
放回來**。如果哪天跑了那支,記得接著再跑這支。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://tntrock.github.io/ntut-course-crawler"


def load(source: str | None, name: str) -> dict:
    if source:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return json.load(resp)


def fetched_at(syllabus: dict) -> dict[tuple[str, str], str]:
    """(學期, 課號) → 大綱抓取時間。已收合的學期沒有逐課狀態,查不到就算了。"""
    out: dict[tuple[str, str], str] = {}
    for semester, courses in (syllabus.get("fetched") or {}).items():
        if not isinstance(courses, dict):
            continue
        for cid, entry in courses.items():
            at = entry.get("at") if isinstance(entry, dict) else entry
            if isinstance(at, str):
                out[(semester, cid)] = at
    return out


def dropped_removals(before: dict | None, current: dict) -> set[tuple[str, str]]:
    """上一次清理已經刪掉的停開,(學期, 課號)。

    在**清理前**的事件流裡是 `course_removed`、現在不在了 —— 那就是上一次
    拿去跟學校對照、確認課還在課表上的那一批(`drop_phantom_removals.py`)。
    它們回來時留下的加開是同一個 bug 的另一半。
    """
    if not before:
        return set()

    def removals(payload: dict) -> set[tuple[str, str]]:
        return {
            (e.get("semester"), e.get("id"))
            for e in payload.get("events") or []
            if e.get("type") == "course_removed"
        }

    return removals(before) - removals(current)


def reason(
    event: dict,
    stamps: dict[tuple[str, str], str],
    paired: set[tuple[str, str]],
) -> str | None:
    """這筆事件是假加開的理由,不是假加開就回 None。"""
    if event.get("type") != "course_added":
        return None
    key = (event.get("semester"), event.get("id"))

    at = stamps.get(key)
    if at and at < event.get("at", ""):
        return f"大綱早在 {at} 就抓過了"
    if key in paired:
        return "跟上次清掉的假停開是同一門課"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changes", help="本機的 changes.json(預設讀線上)")
    parser.add_argument("--syllabus", help="本機的 syllabus.json(預設讀線上)")
    parser.add_argument(
        "--paired-with",
        metavar="FILE",
        help="清理前的 changes.json(啟用規則二,見 docstring)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只列出會刪掉什麼,不輸出檔案"
    )
    parser.add_argument("--out", help="輸出路徑(UTF-8 + LF)")
    args = parser.parse_args()

    if not args.dry_run and not args.out:
        parser.error("要嘛 --dry-run,要嘛用 --out 指定輸出路徑")

    changes = load(args.changes, "changes.json")
    stamps = fetched_at(load(args.syllabus, "syllabus.json"))
    before = json.loads(Path(args.paired_with).read_text(encoding="utf-8")) \
        if args.paired_with else None
    paired = dropped_removals(before, changes)
    if args.paired_with:
        print(f"上次清掉的假停開:{len(paired)} 筆", file=sys.stderr)

    events = changes.get("events") or []
    verdicts = [(e, reason(e, stamps, paired)) for e in events]
    phantom = [e for e, why in verdicts if why]

    if args.dry_run:
        print(f"事件總數 {len(events)},判定為假加開 {len(phantom)} 筆:", file=sys.stderr)
        for e, why in verdicts:
            if why:
                print(
                    f"  {e['at']}  {e['id']}  {e.get('name')}   ← {why}",
                    file=sys.stderr,
                )
        return 0

    if not phantom:
        print("沒有要移除的事件,不輸出", file=sys.stderr)
        return 1

    changes["events"] = [e for e, why in verdicts if not why]
    text = json.dumps(changes, ensure_ascii=False, separators=(",", ":")) + "\n"
    # 明確 UTF-8 + LF。這個 repo 的檔案是 LF,而 Windows 的預設編碼是 cp950。
    Path(args.out).write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))
    print(f"移除 {len(phantom)} 筆假加開 → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
