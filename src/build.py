"""Turn the raw snapshots into flat tables for Power BI.

Writes jobs.csv (one row per advert), skills.csv (one row per advert and skill)
and skill_summary.csv (median salary per skill, against the seniority baseline).

Run: python src/build.py
"""

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw" / "source=nofluffjobs"

POLAND = {"POL", "pl"}

# The source spells the same city in several ways.
CITY_FIXES = {
    "Warsaw": "Warszawa",
    "Cracow": "Kraków",
    "Krakow": "Kraków",
    "Poznan": "Poznań",
    "Gdansk": "Gdańsk",
    "Wroclaw": "Wrocław",
    "Lodz": "Łódź",
    "Bielsko - Biała": "Bielsko-Biała",
}

SENIORITY_ORDER = ["Trainee", "Junior", "Mid", "Senior", "Expert"]

# The same technology is tagged under several names.
SKILL_FIXES = {
    "Golang": "Go",
    "ML": "Machine learning",
    "Postgres": "PostgreSQL",
    "JS": "JavaScript",
    "Kubernetes": "K8s",
}


def read_snapshot(directory):
    """All postings of one day, as returned by the API."""
    postings = []
    for path in sorted(directory.glob("page_*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as file:
            postings += json.load(file)["postings"]
    return postings


def collapse(postings):
    """One record per advert. The API repeats an advert once per location."""
    adverts = {}
    for posting in postings:
        if posting["reference"] not in adverts:
            adverts[posting["reference"]] = posting
    return adverts


def city_of(posting):
    """First Polish city of an advert, or None if it is remote-only."""
    for place in posting["location"]["places"]:
        if place.get("provinceOnly") or not place.get("city"):
            continue
        if place["city"] == "Remote":
            continue
        if (place.get("country") or {}).get("code") not in POLAND:
            continue
        return CITY_FIXES.get(place["city"], place["city"])
    return None


def is_remote(posting):
    return any(place.get("city") == "Remote" for place in posting["location"]["places"])


def lowest_seniority(posting):
    """An advert can list several levels; keep the entry bar it advertises."""
    levels = [level for level in SENIORITY_ORDER if level in posting["seniority"]]
    return levels[0] if levels else None


def job_row(posting, snapshot_date):
    salary = posting["salary"]
    return {
        # An advert reappears in every snapshot, so the row key needs the date.
        "job_key": f"{posting['reference']}-{snapshot_date}",
        "reference": posting["reference"],
        "snapshot_date": snapshot_date,
        "title": posting["title"],
        "company": posting["name"],
        "category": posting["category"],
        "seniority": lowest_seniority(posting),
        "city": city_of(posting),
        "remote": is_remote(posting),
        "contract": salary.get("type"),
        "salary_from": salary.get("from"),
        "salary_to": salary.get("to"),
        "posted": pd.to_datetime(posting["posted"], unit="ms").date(),
    }


def skill_rows(posting, snapshot_date):
    for tile in posting["tiles"]["values"]:
        if tile["type"] == "requirement":
            yield {
                "job_key": f"{posting['reference']}-{snapshot_date}",
                "reference": posting["reference"],
                "snapshot_date": snapshot_date,
                "skill": SKILL_FIXES.get(tile["value"], tile["value"]),
            }


def median_interval(salaries, draws=4000, seed=0):
    """95% bootstrap interval for a median.

    A skill is measured on a few dozen adverts, where a median moves by
    thousands of złoty if one advert changes. Resampling the adverts with
    replacement shows how much of the premium is real and how much is the
    sample being small.
    """
    rng = np.random.default_rng(seed)
    resampled = rng.choice(salaries, size=(draws, len(salaries)))
    medians = np.median(resampled, axis=1)
    return np.percentile(medians, [2.5, 97.5])


def skill_summary(jobs, skills, min_adverts=15):
    """Median salary per skill and seniority, against the seniority baseline.

    Comparing a skill against everyone is misleading, because skills that show up
    in senior adverts would look valuable on their own. The baseline here is the
    median of the same seniority, so the premium is what the skill adds on top.
    """
    joined = skills.merge(jobs, on="job_key")
    baseline = jobs.groupby("seniority")["salary_from"].median()

    rows = []

    for (skill, seniority), group in joined.groupby(["skill", "seniority"]):
        if len(group) < min_adverts:
            continue

        salaries = group["salary_from"].values
        base = baseline[seniority]
        low, high = median_interval(salaries)

        rows.append(
            {
                "skill": skill,
                "seniority": seniority,
                "adverts": len(group),
                "median_salary": np.median(salaries),
                "baseline_salary": base,
                "premium_pct": round((np.median(salaries) / base - 1) * 100, 1),
                "premium_low": round((low / base - 1) * 100, 1),
                "premium_high": round((high / base - 1) * 100, 1),
            }
        )

    summary = pd.DataFrame(rows)
    # Only premiums whose interval stays on one side of zero can be read as real.
    summary["significant"] = (summary["premium_low"] > 0) | (summary["premium_high"] < 0)

    return summary.sort_values("premium_pct", ascending=False)


def main():
    jobs = []
    skills = []

    for directory in sorted(RAW_DIR.glob("date=*")):
        snapshot_date = directory.name.removeprefix("date=")
        adverts = collapse(read_snapshot(directory))

        for posting in adverts.values():
            jobs.append(job_row(posting, snapshot_date))
            skills += list(skill_rows(posting, snapshot_date))

        print(f"{snapshot_date}: {len(adverts)} adverts")

    jobs = pd.DataFrame(jobs)
    # An advert can carry both names of a merged skill, e.g. Go and Golang.
    skills = pd.DataFrame(skills).drop_duplicates(subset=["job_key", "skill"])

    # A zero lower bound is a placeholder, not a salary.
    jobs = jobs[jobs["salary_from"] > 0]
    skills = skills[skills["job_key"].isin(jobs["job_key"])]

    summary = skill_summary(jobs, skills)

    jobs.to_csv(DATA_DIR / "jobs.csv", index=False)
    skills.to_csv(DATA_DIR / "skills.csv", index=False)
    summary.to_csv(DATA_DIR / "skill_summary.csv", index=False)

    print(f"jobs.csv:          {len(jobs)} rows")
    print(f"skills.csv:        {len(skills)} rows")
    print(f"skill_summary.csv: {len(summary)} rows")


if __name__ == "__main__":
    main()
