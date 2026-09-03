"""Phase 0 recon script.

Fetches a small number of real pages from the NTUT course system to determine
encoding and HTML structure before any parser is written. Deliberately slow:
single-threaded, one request at a time, with a mandatory delay between
requests. Do NOT remove the delay or parallelize this script.
"""
from __future__ import annotations

import re
import sys
import time
import hashlib
from pathlib import Path
from urllib.parse import urljoin

import truststore
truststore.inject_into_ssl()  # verify via OS cert store; school's cert trips
                               # OpenSSL's strict-mode SKI check but is valid

import requests

BASE = "https://aps.ntut.edu.tw/course/tw/"
UA = "ntut-course-crawler/0.1 (recon; contact: a940125@gmail.com)"
DELAY_SECONDS = 2.0  # deliberately generous for a one-off recon run
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

session = requests.Session()
session.headers.update({"User-Agent": UA})


def polite_get(url: str) -> requests.Response:
    print(f"  GET {url}")
    resp = session.get(url, timeout=(10, 30))
    resp.raise_for_status()
    print(f"    status={resp.status_code} encoding={resp.encoding} "
          f"apparent_encoding={resp.apparent_encoding} "
          f"content-type={resp.headers.get('Content-Type')}")
    print(f"    sleeping {DELAY_SECONDS}s before next request...")
    time.sleep(DELAY_SECONDS)
    return resp


def save_fixture(name: str, content: bytes) -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{name}.html"
    path.write_bytes(content)
    print(f"    saved -> {path} ({len(content)} bytes)")
    return path


def try_decode(label: str, content: bytes) -> None:
    print(f"    --- decode attempts for {label} ---")
    for enc in ("cp950", "big5", "utf-8"):
        try:
            text = content.decode(enc, errors="strict")
            sample = re.sub(r"\s+", " ", text)[:200]
            print(f"    [{enc}] OK  sample: {sample}")
        except UnicodeDecodeError as e:
            print(f"    [{enc}] FAILED: {e}")


def find_first_href(html_bytes: bytes, pattern: str) -> str | None:
    # Deliberately crude regex scan at this recon stage; real parsing
    # happens later with BeautifulSoup once structure is confirmed.
    text = html_bytes.decode("cp950", errors="replace")
    m = re.search(rf'href="([^"]*{pattern}[^"]*)"', text, re.IGNORECASE)
    return m.group(1) if m else None


def main() -> int:
    print("=== Phase 0 recon ===")
    print(f"User-Agent: {UA}")
    print(f"Delay between requests: {DELAY_SECONDS}s\n")

    # 1. Subj.jsp overview page
    print("[1/3] Subj.jsp overview")
    subj_url = urljoin(BASE, "Subj.jsp?format=-2&year=115&sem=1")
    r1 = polite_get(subj_url)
    save_fixture("subj_overview", r1.content)
    try_decode("subj_overview", r1.content)

    # 2. first department link found on the overview page
    print("\n[2/3] first department link")
    dept_href = find_first_href(r1.content, ".jsp")
    if not dept_href:
        print("    !! could not find any .jsp link on overview page, stopping")
        return 1
    dept_url = urljoin(subj_url, dept_href)
    r2 = polite_get(dept_url)
    save_fixture("dept_page", r2.content)
    try_decode("dept_page", r2.content)

    # 3. first ShowSyllabus.jsp link found on the department page
    print("\n[3/3] first ShowSyllabus.jsp link")
    syllabus_href = find_first_href(r2.content, "ShowSyllabus.jsp")
    if not syllabus_href:
        print("    !! no ShowSyllabus.jsp link found on dept page, "
              "skipping (structure may differ from assumption)")
    else:
        syllabus_url = urljoin(dept_url, syllabus_href)
        r3 = polite_get(syllabus_url)
        save_fixture("syllabus_page", r3.content)
        try_decode("syllabus_page", r3.content)

    print("\n=== done ===")
    print(f"Fixtures written to: {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
