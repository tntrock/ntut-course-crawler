"""發布出去的網頁:首頁與狀態 / 錯誤 / 異動三頁。

這幾頁是給人看的,壞掉不會讓爬蟲少抓一門課 —— 但有兩種錯法會**安靜地**
發生,而且本機怎麼看都是好的:

1. 頁面 fetch 一個根本沒發布到 gh-pages 根目錄的檔名,或不小心寫成
   絕對網址(fork 出去的人就會一直讀原作者的資料)。
2. 加了新頁,卻忘了讓 workflow 把它複製過去 —— 檔在 repo 裡,線上沒有。

兩種都是「線上壞掉、pytest 全綠」。這個檔就是守這兩件事,不驗版面長相。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
WORKFLOW_DIR = ROOT / ".github" / "workflows"

#: 發布到 gh-pages 根目錄的 JSON。頁面只能 fetch 這裡面的檔名。
PUBLISHED_JSON = {
    "meta.json",
    "index.json",
    "runs.json",
    "errors.json",
    "changes.json",
    "enrollment.json",
    "syllabus.json",
    "capacity.json",
}

PAGES = ["index.html", "status.html", "errors.html", "changes.html"]

SHARED_ASSETS = ["style.css", "app.js"]

#: 會發布 gh-pages 的三支 workflow。rebuild 是 crawl.yml 的一個 input,
#: 不是獨立的檔,所以整包重建也走 crawl.yml 這一份。
PUBLISHING_WORKFLOWS = ["crawl.yml", "syllabus.yml", "backfill.yml"]

#: 只認寫死字串的呼叫 —— 直接 fetch(),或走 app.js 的 loadJSON()。
#: 組出來的網址驗不到,也不該有。
FETCH_TARGET = re.compile(r"""(?:fetch|loadJSON)\(\s*["']([^"']+)["']""")


class _Markup(HTMLParser):
    """把測試需要的那幾件事從 HTML 裡撿出來。

    只用標準函式庫:多裝一個解析器只為了驗四個靜態檔並不划算,而且
    HTMLParser 解不開的 HTML 本身就該當成壞掉。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.stylesheets: list[str] = []
        self.script_srcs: list[str] = []
        self.hrefs: list[str] = []
        self.inline_scripts: list[str] = []
        self._collecting: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attr = dict(attrs)
        if tag == "link" and "stylesheet" in (attr.get("rel") or ""):
            self.stylesheets.append(attr.get("href") or "")
        elif tag == "a" and attr.get("href"):
            self.hrefs.append(attr["href"])
        elif tag == "script":
            if attr.get("src"):
                self.script_srcs.append(attr["src"])
            self._collecting = "script"
        elif tag == "title":
            self._collecting = "title"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "title"):
            self._collecting = None

    def handle_data(self, data: str) -> None:
        if self._collecting == "script":
            self.inline_scripts.append(data)
        elif self._collecting == "title":
            self.title += data


def parse(page: str) -> _Markup:
    markup = _Markup()
    markup.feed((WEB_DIR / page).read_text(encoding="utf-8"))
    markup.close()
    return markup


def javascript(page: str) -> str:
    """一個頁面實際會執行的 JS:內嵌的,加上它載入的共用檔。

    <pre><code> 裡的範例程式碼**不算** —— 那是寫給人看的說明,首頁裡
    正好有幾行帶絕對網址的範例,拿整份 HTML 做字串比對會誤判。
    """
    markup = parse(page)
    sources = list(markup.inline_scripts)
    for src in markup.script_srcs:
        # 外部來源(分析工具之類)不是我們寫的程式碼,掃它沒有意義 ——
        # 而且把絕對網址當成 web/ 底下的路徑去讀會直接炸掉。
        if src.startswith(("http://", "https://", "//")):
            continue
        sources.append((WEB_DIR / src).read_text(encoding="utf-8"))
    return "\n".join(sources)


class TestFilesExist:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_exists(self, page: str) -> None:
        assert (WEB_DIR / page).is_file()

    @pytest.mark.parametrize("asset", SHARED_ASSETS)
    def test_every_shared_asset_exists(self, asset: str) -> None:
        assert (WEB_DIR / asset).is_file()


class TestMarkup:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_parses_and_has_a_title(self, page: str) -> None:
        assert parse(page).title.strip()

    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_uses_the_shared_stylesheet(self, page: str) -> None:
        """四頁共用一份樣式,改配色不必四個檔各改一次。"""
        assert "style.css" in parse(page).stylesheets

    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_links_to_every_other_page(self, page: str) -> None:
        """導覽列少一條連結,那一頁就只有知道網址的人找得到。"""
        hrefs = set(parse(page).hrefs)
        for other in PAGES:
            if other != page:
                assert other in hrefs, f"{page} 沒有連到 {other}"


class TestDataSources:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_reads_at_least_one_json(self, page: str) -> None:
        """沒有這條,下面兩條在「一個 fetch 都沒抓到」時會安靜地空轉。"""
        assert FETCH_TARGET.findall(javascript(page))

    @pytest.mark.parametrize("page", PAGES)
    def test_pages_only_fetch_json_that_is_published(self, page: str) -> None:
        for target in FETCH_TARGET.findall(javascript(page)):
            assert target in PUBLISHED_JSON, f"{page} 讀了沒發布的 {target}"

    @pytest.mark.parametrize("page", PAGES)
    def test_pages_fetch_by_relative_path(self, page: str) -> None:
        """寫死絕對網址的話,fork 出去的站會一直顯示原作者的資料。"""
        for target in FETCH_TARGET.findall(javascript(page)):
            assert not target.startswith(("http://", "https://", "//")), target


class TestPublishing:
    @pytest.mark.parametrize("workflow", PUBLISHING_WORKFLOWS)
    def test_every_publishing_workflow_copies_the_whole_web_dir(
        self, workflow: str
    ) -> None:
        """複製整個目錄,而不是逐檔列名 —— 逐檔列的話,下次加頁必定漏掉一支。"""
        text = (WORKFLOW_DIR / workflow).read_text(encoding="utf-8")
        assert re.search(r"cp\s+-R\s+web/\.\s+data/", text), f"{workflow} 沒有發布 web/"


#: Google Analytics 的評估 ID。四個頁面都要帶 —— 少一頁不會有任何錯誤訊息,
#: 只是那頁的流量從此不見。
GA_MEASUREMENT_ID = "G-SJF3YWLFMQ"


class TestAnalytics:
    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_loads_the_analytics_tag(self, page: str) -> None:
        srcs = parse(page).script_srcs
        assert any(
            "googletagmanager.com/gtag/js" in src and GA_MEASUREMENT_ID in src
            for src in srcs
        ), f"{page} 沒有載入 gtag"

    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_configures_the_same_property(self, page: str) -> None:
        """ID 打錯會讓流量進到別的資源,而且同樣不會有錯誤訊息。"""
        inline = "\n".join(parse(page).inline_scripts)
        assert f"gtag('config', '{GA_MEASUREMENT_ID}')" in inline, (
            f"{page} 沒有設定 {GA_MEASUREMENT_ID}"
        )


#: 狀態頁的「各學期資料」表把課表、教學大綱、人數快照三種資料合成一列。
#: 少讀其中一個檔不會報錯,只會讓那一欄整排變成「—」—— 靜默失真最難發現,
#: 所以把來源釘住。
STATUS_SOURCES = {
    "meta.json",
    "syllabus.json",
    "enrollment.json",
    "runs.json",
    "errors.json",
    "capacity.json",
}


class TestStatusPageSources:
    def test_status_page_reads_every_source_it_displays(self) -> None:
        targets = set(FETCH_TARGET.findall(javascript("status.html")))
        missing = STATUS_SOURCES - targets
        assert not missing, f"狀態頁少讀了 {sorted(missing)},對應的欄位會整排變空"


class TestSemesterTableIsOneTable:
    """展開的歷史學期必須跟上面同一張表。

    原本把多出來的列放進 `<details>` 裡的**第二張** `<table>`(因為 details
    不能合法包住 tr),兩張表各自計算欄寬,展開後每一欄都對不齊,而且第二張
    沒有表頭,更難讀。

    修法是全部放同一張表、超出的列先 hidden、用按鈕切換 —— 同一張表的欄寬
    必然一致。這條測試就是釘住「不准再拆成兩張」。
    """

    def semesters_block(self) -> str:
        src = (WEB_DIR / "status.html").read_text(encoding="utf-8")
        start = src.index('block("semesters"')
        return src[start : src.index('block("enrollment"', start)]

    def test_semester_data_renders_in_a_single_table(self) -> None:
        count = self.semesters_block().count("<table>")
        assert count == 1, f"各學期資料用了 {count} 張表格,展開後會對不齊"


#: 頁面允許讀的、位在「學期目錄底下」的 JSON。跟 PUBLISHED_JSON 一樣是白名單,
#: 差別在這些檔的網址一定是組出來的 —— 學期是從資料裡讀到的,寫不死。
#:
#: 組網址本來正是這個檔要擋的事,但班級名稱沒有別條路:changes.json 只存代號,
#: 名稱在各學期的 classes.json 裡,而事件可能橫跨多個學期。所以規則放寬成
#: 「可以組,但檔名必須在白名單裡」—— 檔名打錯的下場是線上 404、頁面不報錯,
#: 只是所有班級安靜地退回顯示代號,跟這次要修的毛病一模一樣。
PUBLISHED_SEMESTER_JSON = {"classes.json"}

#: 只認 `loadJSON(學期變數 + "/檔名.json")` 這一種組法。
SEMESTER_FETCH_TARGET = re.compile(
    r"""(?:fetch|loadJSON)\(\s*\w+\s*\+\s*["']/([^"']+)["']"""
)


class TestSemesterScopedSources:
    @pytest.mark.parametrize("page", PAGES)
    def test_pages_only_fetch_semester_json_that_is_published(self, page: str) -> None:
        for target in SEMESTER_FETCH_TARGET.findall(javascript(page)):
            assert target in PUBLISHED_SEMESTER_JSON, f"{page} 讀了沒發布的 {target}"

    def test_changes_page_looks_up_class_names(self) -> None:
        """異動頁要把班級代號換成名稱,對照表只在各學期的 classes.json 裡。

        少了這個 fetch 不會有任何錯誤 —— 查表查不到就退回代號,整頁看起來
        「正常」,只是又變回滿版的 3014、2519。
        """
        targets = SEMESTER_FETCH_TARGET.findall(javascript("changes.html"))
        assert "classes.json" in targets, "異動頁沒讀 classes.json,班級會退回代號"


class TestClassIdsRenderAsNames:
    """班級代號出現在異動頁的三個地方,少改一個就是那一處還在顯示數字。"""

    def changes_js(self) -> str:
        return javascript("changes.html")

    def test_course_events_do_not_join_class_ids_raw(self) -> None:
        """課程事件那一行的「· 班級 ⋯」。直接 join 陣列就是印代號。"""
        assert "e.class_ids.join(" not in self.changes_js()

    def test_bulk_change_tally_translates_class_ids(self) -> None:
        """整批異動的「依班級」統計,key 本身就是代號,要帶 formatter 進去。"""
        src = self.changes_js()
        start = src.index("tally(e.by_class")
        call = src[start : src.index("\n", start)]
        assert "className" in call, f"依班級統計沒有換成名稱:{call}"

    def test_class_ids_field_change_translates_both_sides(self) -> None:
        """course_changed 的「修課班級:A → B」,兩邊都是代號陣列。

        這一欄跟其他欄共用同一個 value(),所以一定要看欄位名分流,
        不能整批套 —— 上課時間、學分那些欄位沒有班級可查。
        """
        assert 'f === "class_ids"' in self.changes_js(), (
            "course_changed 的 class_ids 欄位沒有換成名稱"
        )
