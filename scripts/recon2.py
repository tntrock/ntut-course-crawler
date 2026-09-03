"""Phase 0 recon, round 2: follow-up after round 1 revealed the real
hierarchy is one level deeper than the spec assumed (format=-2 -> -3 -> -4),
and that the first link on the overview page ("教務處", code=01) was an
administrative unit, not an academic department. This targets a real
department (資工系, code=59) to see the -3 and -4 levels for a typical case.

Same rate-limit discipline as recon.py: sequential, single request at a
time, mandatory delay between requests.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import truststore
truststore.inject_into_ssl()

import requests

BASE = "https://aps.ntut.edu.tw/course/tw/"
UA = "ntut-course-crawler/0.1 (recon; contact: a940125@gmail.com)"
DELAY_SECONDS = 2.0
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

session = requests.Session()
session.headers.update({"User-Agent": UA})


def polite_get(url: str) -> requests.Response:
    print(f"  GET {url}")
    resp = session.get(url, timeout=(10, 30))
    resp.raise_for_status()
    print(f"    status={resp.status_code} encoding={resp.encoding}")
    print(f"    sleeping {DELAY_SECONDS}s before next request...")
    time.sleep(DELAY_SECONDS)
    return resp


def save_fixture(name: str, content: bytes) -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{name}.html"
    path.write_bytes(content)
    print(f"    saved -> {path} ({len(content)} bytes)")
    return path


def find_first_href(html_bytes: bytes, pattern: str) -> str | None:
    text = html_bytes.decode("utf-8", errors="replace")
    m = re.search(rf'href="([^"]*{pattern}[^"]*)"', text, re.IGNORECASE)
    return m.group(1) if m else None


def main() -> int:
    print("=== Phase 0 recon round 2 (資工系, code=59) ===\n")

    print("[1/2] format=-3 department page (資工系)")
    dept_url = urljoin(BASE, "Subj.jsp?format=-3&year=115&sem=1&code=59")
    r1 = polite_get(dept_url)
    save_fixture("dept_page_real", r1.content)

    print("\n[2/2] format=-4 course listing page")
    course_href = find_first_href(r1.content, r"format=-4")
    if not course_href:
        print("    !! no format=-4 link found, dumping page for inspection")
        print(r1.content.decode("utf-8", errors="replace")[:2000])
        return 1
    course_url = urljoin(dept_url, course_href)
    r2 = polite_get(course_url)
    save_fixture("course_list_real", r2.content)

    syllabus_href = find_first_href(r2.content, "ShowSyllabus.jsp")
    print(f"\n  ShowSyllabus.jsp link found on course page: {syllabus_href}")

    print("\n=== done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
