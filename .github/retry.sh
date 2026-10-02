#!/usr/bin/env bash
# 用法:retry.sh <最多幾次> <每次間隔秒數> <指令...>
#
# 失敗就等一段時間整批重來。crawler 內部的重試是「單一請求」層次的
# (8 次、約 7.5 分鐘),治得了線路抖動,治不了實際遇過的幾十分鐘中斷 ——
# 2026-09-04 08:33 UTC 那次 runner 連不到學校近一小時,同一時間從台灣連只要
# 17 毫秒。重試很便宜:.cache/ 在同一個 job 內共用,已抓到的頁面不會再打。
#
# 跑了幾次寫進 CRAWL_ATTEMPTS,給 Record this run 用:「一次就過」和
# 「重試三次才過」對狀態頁是兩回事。放棄的那條路徑也要寫。
set -u
max=$1 wait=$2
shift 2
attempt=1
until "$@"; do
  if [ "$attempt" -ge "$max" ]; then
    echo "::error::連續 $attempt 次失敗,放棄本次執行(已完成的部分照樣發布,下一班會接續)"
    echo "CRAWL_ATTEMPTS=$attempt" >> "$GITHUB_ENV"
    exit 1
  fi
  echo "::warning::第 $attempt 次失敗,等 $((wait / 60)) 分鐘後整批重來"
  sleep "$wait"
  attempt=$((attempt + 1))
done
echo "CRAWL_ATTEMPTS=$attempt" >> "$GITHUB_ENV"
