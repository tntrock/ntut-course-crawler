# 為什麼兩支 workflow 都要先「還原」三個檔案

`crawl.yml`(每 4 小時)與 `backfill.yml`(手動回補)都會在抓取前，
從 gh-pages 撈回 `meta.json` / `index.json` / `errors.json` 這三個**跨學期**的檔案。

原因是 `write_outputs()` 每次都會重寫它們，而重寫的方式是「讀舊的 → 換掉本次學年期的部分 → 寫回去」。
沒有舊檔可讀的話：

- `meta.json` 會只剩下本次抓的學期，其他 50 個學期在 API 上等於消失
- `index.json` 的 `covers` 會算錯，把線上正確的索引蓋掉
- `errors.json` 會丟掉其他學期的錯誤紀錄
- `--refresh-after` / 回補的「這學期抓過了沒」判斷失去依據，每次都重抓一遍

**只撈這三個檔，不是整包。** 歷史資料有數百 MB，每 4 小時 clone 一次是浪費；
而且它們也不需要在本機出現 —— 發布時用 `keep_files: true`，
不在 `publish_dir` 裡的檔案會原封不動留在分支上。

用 `--filter=blob:none --no-checkout` 做 partial clone，只下載這三個檔的內容，
不動其餘幾千個檔案的 blob。
