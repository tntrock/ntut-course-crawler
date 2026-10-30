# 安全政策

## 支援的版本

只維護 `main` 的最新版本,以及 GitHub Pages 上目前發布的資料。

## 回報漏洞

**請不要開公開的 issue。**

到 [Security 分頁](https://github.com/tntrock/ntut-course-crawler/security/advisories/new)
按「Report a vulnerability」私下回報,內容請附上:

- 問題所在(檔案、workflow 或發布出去的資料路徑)
- 重現步驟
- 可能造成的影響

會在 7 天內回覆。確認是問題的話會私下修好,修完再公開 advisory。

## 範圍

- 爬蟲程式(`crawler/`)與網頁(`web/`)
- GitHub Actions workflow,例如權限過大、可被注入的輸入
- 發布的 JSON 中不該公開的資料

學校課程查詢系統本身的問題不在這個專案的範圍內,請直接聯絡學校。
