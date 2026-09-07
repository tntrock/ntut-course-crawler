"""單位頁偶發少列班級,不可以讓那個班級的課被判定成停開。

線上實際發生過(2026-09-07 09:47):異動頁一次冒出 10 筆「停開」,全部是
單位 14 的課、全部屬於同一個班級群組 2764。去學校頁面查證,2764 還在、
底下正好就是那 10 門課 —— 課根本沒停開。

比對兩次執行的統計就看得出來:
    09:34  115-1 抓到 2719 門,課表請求 355 個
    09:56  115-1 抓到 2709 門,課表請求 350 個
兩次都是 departments_ok=60、departments_failed=0、errors=0。少的 5 個請求
就是 5 個沒被列出來的班級 —— **沒有任何東西失敗**,所以一個錯誤都沒記。

`_crawl_department()` 只有在 fetch 丟例外時才記錯誤。單位頁少列一個連結
是「解析成功,只是內容變少」,整條錯誤路徑完全不會被觸發,而那個班級底下
只出現在該處的課就直接從資料集消失,再被異動偵測記成停開。

修法跟 `SiteUnavailable` 那條守則同源(見 crawl() 裡的註解:「把這種半套
結果寫出去會蓋掉線上完整的資料」),只是下沉到班級層級:拿上一輪的
`<學期>/classes.json` 當底,單位頁沒列到的班級照樣去抓 —— 班級代碼本身
就足以取得課表(`format=-4&code=2764` 單獨打得通,實測回傳那 10 門課)。
"""

from __future__ import annotations

import json

from crawler.main import crawl, main
from crawler.models import ClassGroup
from crawler.output import read_class_groups, write_outputs
from tests.test_main import FakeFetcher, fake_fetcher_factory  # noqa: F401

#: dept_page_real.html 裡資工系(59)的五個班級。
ALL_GROUPS = {"2915", "3032", "3138", "3718", "3743"}
DROPPED = "3743"


def known(*ids):
    return {
        "59": [
            ClassGroup(
                id=gid,
                name=f"班級{gid}",
                department_id="59",
                url=f"https://aps.ntut.edu.tw/course/tw/Subj.jsp?format=-4&code={gid}",
            )
            for gid in ids
        ]
    }


def fetched_groups(fetcher):
    return {code for fmt, code in fetcher.calls if fmt == -4}


class TestTheFakeFetcherCanDropGroups:
    """先確認測試工具本身真的會少列 —— 不然下面每一條都會空轉通過。"""

    def test_dropping_removes_it_from_the_page(self):
        fetcher = FakeFetcher(drop_class_groups={DROPPED})
        crawl(fetcher, 115, 1, only_departments=["59"])
        assert fetched_groups(fetcher) == ALL_GROUPS - {DROPPED}

    def test_without_dropping_every_group_is_fetched(self):
        fetcher = FakeFetcher()
        crawl(fetcher, 115, 1, only_departments=["59"])
        assert fetched_groups(fetcher) == ALL_GROUPS


class TestGroupsMissingFromThePageAreStillCrawled:
    def test_the_omitted_group_is_fetched_anyway(self):
        fetcher = FakeFetcher(drop_class_groups={DROPPED})
        crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_groups=known(*ALL_GROUPS),
        )
        assert DROPPED in fetched_groups(fetcher), (
            "單位頁少列的班級沒有被補抓,它底下的課會被記成停開"
        )

    def test_it_still_lands_in_the_result(self):
        fetcher = FakeFetcher(drop_class_groups={DROPPED})
        result = crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_groups=known(*ALL_GROUPS),
        )
        assert DROPPED in {g.id for g in result.class_groups["59"]}

    def test_the_omission_is_recorded_as_an_error(self):
        """靜默是這個 bug 最貴的部分 —— 補抓了也要留下痕跡。"""
        fetcher = FakeFetcher(drop_class_groups={DROPPED})
        result = crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_groups=known(*ALL_GROUPS),
        )
        matches = [
            e for e in result.errors
            if e.get("class_group_id") == DROPPED
        ]
        assert matches, "少列班級沒有記進 errors,下次還是查無對證"
        assert matches[0]["stage"] == "class_group_missing"
        assert matches[0]["department_id"] == "59"

    def test_groups_the_page_does_list_are_not_fetched_twice(self):
        fetcher = FakeFetcher()
        crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_groups=known(*ALL_GROUPS),
        )
        codes = [code for fmt, code in fetcher.calls if fmt == -4]
        assert len(codes) == len(set(codes)) == len(ALL_GROUPS)

    def test_a_page_that_lists_everything_records_no_error(self):
        fetcher = FakeFetcher()
        result = crawl(
            fetcher, 115, 1,
            only_departments=["59"],
            known_groups=known(*ALL_GROUPS),
        )
        assert [e for e in result.errors if e["stage"] == "class_group_missing"] == []

    def test_a_brand_new_group_needs_no_history(self):
        """上一輪沒看過的班級照樣要抓 —— 補強不能變成白名單。"""
        fetcher = FakeFetcher()
        crawl(fetcher, 115, 1, only_departments=["59"], known_groups=known("2915"))
        assert fetched_groups(fetcher) == ALL_GROUPS

    def test_without_history_the_behaviour_is_unchanged(self):
        fetcher = FakeFetcher(drop_class_groups={DROPPED})
        result = crawl(fetcher, 115, 1, only_departments=["59"])
        assert fetched_groups(fetcher) == ALL_GROUPS - {DROPPED}
        assert result.errors == []


class TestReadClassGroups:
    """上一輪的班級名單從 `<學期>/classes.json` 讀回來。"""

    def test_round_trip(self, tmp_path):
        write_outputs(crawl(FakeFetcher(), 115, 1, only_departments=["59"]), tmp_path)
        groups = read_class_groups(tmp_path, "115-1")
        assert {g.id for g in groups["59"]} == ALL_GROUPS
        assert all(g.department_id == "59" for g in groups["59"])

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert read_class_groups(tmp_path, "115-1") == {}

    def test_corrupt_file_is_empty_not_an_exception(self, tmp_path):
        (tmp_path / "115-1").mkdir()
        (tmp_path / "115-1" / "classes.json").write_text("{壞掉", encoding="utf-8")
        assert read_class_groups(tmp_path, "115-1") == {}

    def test_entries_without_a_department_are_skipped(self, tmp_path):
        """`_write_classes()` 對認不出單位的班級會寫 department_id: null。"""
        (tmp_path / "115-1").mkdir()
        (tmp_path / "115-1" / "classes.json").write_text(
            json.dumps({"classes": [
                {"id": "1", "name": "孤兒", "department_id": None, "url": None},
                {"id": "2", "name": "正常", "department_id": "59", "url": "u"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
        groups = read_class_groups(tmp_path, "115-1")
        assert list(groups) == ["59"]
        assert [g.id for g in groups["59"]] == ["2"]


class TestMainUsesThePreviousRunsGroups:
    """整條接起來:第一次抓寫下 classes.json,第二次少列時靠它補回來。"""

    def test_the_second_run_recovers_the_dropped_group(
        self, tmp_path, fake_fetcher_factory
    ):
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "CRITICAL"])
        assert (tmp_path / "115-1" / "classes.json").is_file()

        fake_fetcher_factory.drop_class_groups = {DROPPED}
        main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
              "--dept", "59", "--log-level", "CRITICAL"])

        second = fake_fetcher_factory.created[-1]
        assert DROPPED in fetched_groups(second), (
            "第二次沒把上一輪的班級名單帶進來,少列的班級就這樣消失了"
        )

    def test_the_first_run_has_no_history_and_still_works(
        self, tmp_path, fake_fetcher_factory
    ):
        fake_fetcher_factory.drop_class_groups = {DROPPED}
        code = main(["--year", "115", "--sem", "1", "--out", str(tmp_path),
                     "--dept", "59", "--log-level", "CRITICAL"])
        assert code == 0
