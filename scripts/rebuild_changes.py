"""從 gh-pages 的歷史版本重建 `changes.json` 的事件流。

**一次性的維護腳本,不參與正式流程。**

為什麼會需要它
--------------
事件流是 append-only 的:寫下去的事件不會被後續執行改寫。所以 2026-09-04
10:24 那筆在「bulk_change 帶分組統計」上線前 22 分鐘寫入的事件,只有一個
總數,沒有 `by_department` / `by_class` / `samples` —— 而它會一直卡在最前面,
要幾個月後才被 500 筆的上限擠掉。前端做「最近異動」頁時第一眼就會撞到。

與其等,不如用**現在的比對程式碼**對著 gh-pages 上實際發布過的每個版本
重跑一次。學期 index.json 的每個 commit 就是一次發布,兩兩相鄰做 diff
即可還原整段歷史。只發布過一次的學期(回補下來的歷史學期)直接跳過 ——
它們沒有異動史,硬生 baseline 只會洗版。

`at` 用 gh-pages 的 commit 時間 —— 那就是我們發布那份資料的時間,與正式
流程裡 `at` 的語意(偵測到的時間)一致,不是編造的。

用法
----
    git clone --branch gh-pages <repo> /tmp/ghp
    python scripts/rebuild_changes.py /tmp/ghp > changes.json

產出的檔案直接覆蓋 gh-pages 根目錄的 `changes.json` 即可;之後的執行會把它
撈回來繼續往上疊。

注意
----
這是**破壞性**的重建:輸出會取代既有的事件流,而不是合併。只有在既有內容
確實需要修正時才用它,平常不要跑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.config import SCHEMA_VERSION  # noqa: E402
from crawler.output import (  # noqa: E402
    CHANGE_EVENT_LIMIT,
    _collapse_if_bulk,
    _course_events,
    _teacher_events,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")


def semesters(repo: Path, ref: str) -> list[str]:
    """gh-pages 上有哪些學期目錄,新到舊。"""
    names = []
    for line in git(repo, "ls-tree", "--name-only", ref).splitlines():
        name = line.strip().rstrip("/")
        if "-" in name and name.split("-")[0].isdigit():
            names.append(name)

    def key(path: str) -> tuple[int, int]:
        year, _, sem = path.partition("-")
        return int(year), int(sem or 0)

    return sorted(names, key=key, reverse=True)


def revisions(repo: Path, ref: str, path: str) -> list[tuple[str, str]]:
    """(commit, 提交時間),由舊到新。"""
    out = git(repo, "log", "--format=%H %cI", "--reverse", ref, "--", path)
    rows = []
    for line in out.splitlines():
        sha, _, when = line.partition(" ")
        if sha:
            rows.append((sha, when))
    return rows


def courses_at(repo: Path, sha: str, path: str) -> dict[str, dict]:
    raw = git(repo, "show", f"{sha}:{path}")
    return {c["id"]: c for c in json.loads(raw).get("courses", [])}


def to_utc_z(iso: str) -> str:
    return (
        datetime.fromisoformat(iso)
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def rebuild(repo: Path, ref: str = "origin/gh-pages") -> dict:
    events: list[dict] = []
    # 最後一次「比對過」的時間 —— 是最新的**發布**時間,不是最新的事件時間。
    # 兩者常常差很遠(最近幾次跑都沒有異動時,最新事件可能是好幾天前的),
    # 而 checked_at 的用途正是分辨「學校沒動」與「爬蟲沒在跑」。取錯會讓
    # 前端誤判成後者。
    checked_at = ""

    # 只處理發布過兩次以上的學期 —— 見下方的 `len(revs) < 2`。
    for semester in semesters(repo, ref):
        path = f"{semester}/index.json"
        revs = revisions(repo, ref, path)
        if len(revs) < 2:
            # 只發布過一次的學期沒有異動史可言(回補下來的 49 個歷史學期都是
            # 這種)。硬生出一筆 baseline 只會讓「最近異動」頁被 51 筆一模一樣
            # 時間戳的 baseline 洗版,把真正的異動擠掉。
            continue

        checked_at = max(checked_at, to_utc_z(revs[-1][1]))
        first_sha, first_when = revs[0]
        previous = courses_at(repo, first_sha, path)
        events.append(
            {
                "at": to_utc_z(first_when),
                "semester": semester,
                "type": "baseline",
                "course_count": len(previous),
            }
        )
        print(
            f"{semester}:{len(revs)} 個版本,baseline {len(previous)} 門",
            file=sys.stderr,
        )

        for sha, when in revs[1:]:
            current = courses_at(repo, sha, path)
            at = to_utc_z(when)
            batch = _course_events(previous, current, semester, at)
            batch += _teacher_events(previous, current, semester, at)
            batch = _collapse_if_bulk(batch, semester, at)
            events.extend(batch)
            print(
                f"  {at}  {len(previous)} → {len(current)} 門,{len(batch)} 筆事件",
                file=sys.stderr,
            )
            previous = current

    events.sort(key=lambda e: e["at"], reverse=True)
    events = events[:CHANGE_EVENT_LIMIT]
    if not checked_at:
        checked_at = to_utc_z(datetime.now(timezone.utc).isoformat())

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": checked_at,
        "checked_at": checked_at,
        "event_count": len(events),
        "events": events,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    payload = rebuild(Path(argv[1]))
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"\n共 {payload['event_count']} 筆事件", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
