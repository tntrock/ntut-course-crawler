# ntut-course-crawler

北科課程系統爬蟲 + 靜態 API（開發中）

## Phase 0 偵察結論

對照規劃書 §1.3 的推測結構，實際偵察結果如下：

### 編碼

**確認為 UTF-8**，不是規劃書原先推測的 Big5 / cp950。伺服器回應標頭與頁面內容皆一致（`Content-Type: text/html; charset=UTF-8`），中文可直接以 UTF-8 解碼，無需 `errors='replace'` 降級。Phase 1 的 `http.py` 應固定用 UTF-8 decode。

### 實際階層結構（比規劃書深一層）

規劃書原先假設：

```
Subj.jsp?format=-2   ← 學制/系所總覽
  └─ 各系所課表頁
       └─ 課程列表
```

實際上是：

```
Subj.jsp?format=-2&year=115&sem=1          ← 學院/單位總覽（含行政單位如教務處）
  └─ Subj.jsp?format=-3&year=115&sem=1&code={單位代碼}   ← 該單位底下的班級群組列表
       └─ Subj.jsp?format=-4&year=115&sem=1&code={班群代碼}   ← 實際課程列表表格
            └─ ShowSyllabus.jsp?snum={課號}&code={code}       ← 教學大綱
```

`format=-2` 總覽頁第一個連結是「教務處」（code=01，行政單位），不是學院系所；用它往下爬只會看到「遠距教學班」「輔導課程」之類的班群，不是一般系所課程。真正系所（如資工系 code=59）要從總覽頁的院系表格中挑選。`code` 在 `-3`→`-4` 之間會換成另一組數字（例如資工系 `code=59` 底下的班群 `code=2915`），是伺服器端另配的 ID，不能用系所代碼直接拼 `format=-4` 的 URL。

### 課程列表表格結構

`format=-4` 頁面的課程表格為典型老式 `<table>`（`<TR>`/`<TD>` 皆無收尾標籤，`BeautifulSoup` 需搭配 `lxml` parser 容錯），欄位依序為：課號、課程名稱、階段、學分、時數、修(必/選)、教師、日一二三四五六（7 個節次欄位）、教室、人數、撤、授課語言、教學大綱、備註、隨班附讀、實驗實習、跨領域。

節次代碼與規劃書 §2 假設一致，包含 `1-9`、`N`、`A-D`，頁面底部附節次對照時間表：

```
1: 08:10-09:00  2: 09:10-10:00  3: 10:10-11:00  4: 11:10-12:00  N: 12:10-13:00
5: 13:10-14:00  6: 14:10-15:00  7: 15:10-16:00  8: 16:10-17:00  9: 17:10-18:00
A: 18:30-19:20  B: 19:20-20:10  C: 20:20-21:10  D: 21:10-22:00
```

### SSL 憑證問題

Python 內建 `ssl` 模組（OpenSSL 3.x 嚴格模式）會拒絕學校憑證，回報 `CERTIFICATE_VERIFY_FAILED: Missing Subject Key Identifier`；`curl`（走 Windows schannel）與瀏覽器都能正常驗證，判斷是憑證缺少非必要擴充欄位、被 OpenSSL 嚴格模式擋下，非真正的安全性問題。解法：改用 [`truststore`](https://pypi.org/project/truststore/) 套件，讓 Python 走系統憑證庫驗證（與 `curl`/瀏覽器行為一致），而不是關閉憑證驗證。`http.py` 開發時需 `truststore.inject_into_ssl()`。

### Fixtures

`tests/fixtures/` 下已有 5 份真實 HTML 樣本：

- `subj_overview.html` — format=-2 總覽頁
- `dept_page.html` — format=-3，教務處（行政單位範例）
- `dept_page_real.html` — format=-3，資工系（真實系所範例）
- `course_list_real.html` — format=-4，資工系某班群的實際課程列表
- `syllabus_page_real.html` — ShowSyllabus.jsp 教學大綱頁

偵察過程共發出 6 次請求，單執行緒、每次間隔 2 秒，未平行、未加速。

## 待辦

Phase 1（`http.py`）開始前，需要先確認 `format=-3` → `format=-4` 之間 `code` 對應關係的取得方式（目前是從 `-3` 頁面直接解析出 `-4` 的完整連結，不需自己組 code），並更新 `models.py` 的欄位設計以涵蓋上面列出的完整欄位集合（跨領域學程等）。
