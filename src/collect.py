"""Download one daily snapshot of No Fluff Jobs postings into data/raw/.

Run: python src/collect.py
"""

import gzip
import json
import time
from datetime import date
from pathlib import Path

import requests

SEARCH_URL = "https://nofluffjobs.com/api/search/posting"
PAGE_SIZE = 200
MAX_PAGES = 100

# The endpoint needs an explicit currency and period; it also uses them as a
# filter, so the snapshot only contains postings with a published salary.
PARAMS = {
    "limit": PAGE_SIZE,
    "salaryCurrency": "PLN",
    "salaryPeriod": "month",
    "region": "pl",
}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# The API answers 403 without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def fetch_page(page):
    response = requests.post(
        SEARCH_URL,
        params={**PARAMS, "page": page},
        json={"criteriaSearch": {}, "page": page},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main():
    output_dir = RAW_DIR / f"source=nofluffjobs/date={date.today()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0

    for page in range(1, MAX_PAGES + 1):
        payload = fetch_page(page)
        postings = payload["postings"]
        if not postings:
            break

        path = output_dir / f"page_{page:04d}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)

        total += len(postings)
        print(f"page {page}/{payload['totalPages']}: {len(postings)} postings")

        if page >= payload["totalPages"]:
            break

        time.sleep(1)

    print(f"saved {total} postings to {output_dir}")


if __name__ == "__main__":
    main()
