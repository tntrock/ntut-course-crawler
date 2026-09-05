"""把每一次 workflow 執行的結果記進根目錄的 `runs.json`。

為什麼不直接看 GitHub Actions 的 log
------------------------------------
那是給人看的:要嘛登入網頁點進去,要嘛打 API 帶 token,而且**只留 90 天**。
一個靜態的狀態頁沒辦法用它 —— 它需要的是一份跟其他資料放在一起、
直接 `fetch()` 就拿得到的執行紀錄。這個檔就是。

為什麼由 workflow 呼叫,而不是爬蟲自己寫
----------------------------------------
**因為最需要留下紀錄的那幾次,爬蟲根本沒機會寫。** job 逾時是直接把行程
砍掉的;整批重試用完是 shell 迴圈 `exit 1`,那時 Python 早就結束了。
所以這支由 workflow 的 `always()` 步驟呼叫,狀態從 `job.status` 拿 ——
成功、失敗、被取消(逾時算被取消)都會留下一筆。

爬蟲自己知道的細節(抓了哪些學期、幾個請求、幾筆錯誤)走 `--summary`
那個側寫檔。側寫檔不存在(爬蟲在寫出它之前就死了)也照樣記一筆,
只是細節從缺 —— 那本身就是有意義的資訊。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import SCHEMA_VERSION
from .output import _now, _read_json, _write_json

#: `runs.json` 保留幾筆。一天八班,120 筆約兩週。
#:
#: 這個檔每次執行都會重寫,所以它的大小直接換算成 gh-pages 的成長速度 ——
#: 一筆約 400 bytes,120 筆是 50 KB,相對於一次發布本來就有的 3.7 MB
#: 可以忽略。要再長就得重新算這筆帳。
RUN_LOG_LIMIT = 120


def build_record(
    *,
    status: str,
    workflow: str,
    run_id: str,
    attempt: str,
    event: str,
    repository: str,
    server_url: str,
    attempts: str,
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """組一筆執行紀錄。`summary` 是 None 代表爬蟲沒能寫出側寫檔。"""
    record: dict[str, Any] = {
        "at": _now(),
        "workflow": workflow or None,
        "status": (status or "unknown").lower(),
        "event": event or None,
        "run_id": run_id or None,
        "attempt": int(attempt) if str(attempt).isdigit() else None,
        # workflow 的整批重試跑了幾次(不是 GitHub 的 re-run)。
        # 「成功但重試了 3 次」跟「一次就過」對狀態頁是兩回事。
        "attempts": int(attempts) if str(attempts).isdigit() else None,
    }
    if run_id and repository and server_url:
        record["url"] = f"{server_url}/{repository}/actions/runs/{run_id}"

    if summary is None:
        # 側寫檔不存在 = 爬蟲沒跑到寫出它就結束了(被砍、或更早的步驟就失敗)。
        # 明講出來,而不是留一堆 0 讓人以為「跑了但什麼都沒抓到」。
        record["detail"] = False
        return record

    record["detail"] = True
    record["attempt_started_at"] = summary.get("attempt_started_at")
    record["requests_ok"] = summary.get("requests_ok")
    record["failed_urls"] = summary.get("failed_urls")
    record["cache_hits"] = summary.get("cache_hits")
    record["semesters"] = summary.get("semesters") or []
    record["failed_semesters"] = summary.get("failed_semesters") or []
    record["exit_code"] = summary.get("exit_code")
    return record


def append_run(out_dir: Path, record: dict[str, Any], *, pretty: bool = False) -> None:
    """把一筆紀錄放到 `runs.json` 最前面,並砍掉超出保留上限的舊紀錄。"""
    path = Path(out_dir) / "runs.json"
    existing = _read_json(path) or {}
    runs = [r for r in (existing.get("runs") or []) if isinstance(r, dict)]

    # 同一個 run 重試(attempt 2)時換掉自己那一筆,不要疊兩筆
    key = (record.get("run_id"), record.get("attempt"))
    if key != (None, None):
        runs = [r for r in runs if (r.get("run_id"), r.get("attempt")) != key]

    runs.insert(0, record)
    _write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": record["at"],
            "run_count": len(runs[:RUN_LOG_LIMIT]),
            "runs": runs[:RUN_LOG_LIMIT],
        },
        pretty,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler.runlog",
        description="把這次 workflow 執行的結果記進 runs.json",
    )
    parser.add_argument("--out", type=Path, required=True, help="輸出目錄(data/)")
    parser.add_argument(
        "--status", required=True, help="job.status:success / failure / cancelled"
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="爬蟲寫的側寫檔(--run-summary 的路徑)。不存在就只記 job 層級的資訊",
    )
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", ""))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    parser.add_argument(
        "--attempts",
        default=os.environ.get("CRAWL_ATTEMPTS", ""),
        help="workflow 的整批重試跑了幾次(由抓取步驟寫進 GITHUB_ENV)",
    )
    parser.add_argument(
        "--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    summary = None
    if args.summary and args.summary.is_file():
        try:
            summary = json.loads(args.summary.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"側寫檔讀不動({exc}),只記 job 層級的資訊", file=sys.stderr)
    if not isinstance(summary, dict):
        summary = None

    record = build_record(
        status=args.status,
        workflow=args.workflow,
        run_id=args.run_id,
        attempt=args.attempt,
        event=args.event,
        repository=args.repository,
        server_url=args.server_url,
        attempts=args.attempts,
        summary=summary,
    )
    append_run(args.out, record, pretty=args.pretty)
    print(
        f"已記錄:{record['workflow']} {record['status']}"
        f"({'有細節' if record['detail'] else '無細節'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
