# 為什麼每支 workflow 都要先「還原」那幾個檔案

`crawl.yml`(每 4 小時)、`syllabus.yml`(一天兩班)與 `backfill.yml`(手動回補)
都會在抓取前，從 gh-pages 撈回 `meta.json` / `index.json` / `errors.json` /
`changes.json` / `enrollment.json` 這幾個**跨學期**的檔案。
`syllabus.yml` 與 `backfill.yml` 還多撈一個 `syllabus.json`
（哪些課的大綱抓過了、什麼時候抓的、內容雜湊是多少）。

原因是 `write_outputs()` 每次都會重寫它們，而重寫的方式是「讀舊的 → 換掉本次學年期的部分 → 寫回去」。
沒有舊檔可讀的話：

- `meta.json` 會只剩下本次抓的學期，其他 50 個學期在 API 上等於消失
- `index.json` 的 `covers` 會算錯，把線上正確的索引蓋掉
- `errors.json` 會丟掉其他學期的錯誤紀錄
- `changes.json` 會從零開始，等於每次跑完都只剩一筆紀錄，變更歷史留不住；
  而且它的比對基準就是舊的 `index.json`，沒撈回來的話每次都會判成 baseline
- `enrollment.json` 會只剩今天那一筆,人數走勢的索引等於歸零(逐日快照
  本身在學期子目錄裡,靠 `keep_files` 留著,不會掉)
- `syllabus.json` 沒撈回來的話，每一班的大綱抓取都會從頭抓 1,909 頁，
  而且進度顯示會歸零（大綱明細本身在學期子目錄裡，靠 `keep_files` 留著）。
  它同時是「內容有沒有變」的唯一依據 —— 少了它，即使一個字都沒改，
  1,909 份大綱也會全部重寫一次，那正是 v3 要消掉的東西。
  對 `backfill.yml` 還多一層意義：`frozen` 區塊記著哪些歷史學期的大綱補完了，
  沒撈回來的話每一批都會把補過的學期再補一次
- `--refresh-after` / 回補的「這學期抓過了沒」判斷失去依據，每次都重抓一遍

**只撈這幾個檔，不是整包。** 歷史資料有數百 MB，每 4 小時 clone 一次是浪費；
而且它們也不需要在本機出現 —— 發布時用 `keep_files: true`，
不在 `publish_dir` 裡的檔案會原封不動留在分支上。

用 `--depth 1 --filter=blob:none --sparse` 做 partial clone。`--sparse` 的預設
sparse-checkout 只取**根目錄下的檔案**，正好就涵蓋這幾個；歷史學期在子目錄裡的
幾千個檔連 blob 都不會下載。

> 不要改用 `--no-checkout` 搭配 `git checkout HEAD -- <path>`。blobless clone 在
> 那條路徑上不會觸發 lazy fetch，只會以 `error: unable to read sha1 file` 收場
> —— 這個組合實際踩過。
