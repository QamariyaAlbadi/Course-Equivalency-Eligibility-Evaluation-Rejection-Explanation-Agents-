"""Fills app/knowledge_base/study_plans.json from the University of Sharjah degree pages.

    python -m app.tools.fetch_study_plans

Each degree page lists its courses in HTML tables (course number, title, credit hours). The
parser does not depend on the page layout: every table row whose first cell is a 7-digit course
number is read as a course, so the mandatory, core and elective tables are all picked up.
Run it again whenever the university updates its study plan. Review the printed counts, and open
the JSON to spot-check a few courses before relying on it.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PAGES = {
    "Computer Science": "https://www.sharjah.ac.ae/Academics/Degree/Undergraduate/Computer-Science",
    "Computer Engineering": "https://www.sharjah.ac.ae/Academics/Degree/Undergraduate/Computer-Engineering",
}
OUT = Path(__file__).resolve().parent.parent / "knowledge_base" / "study_plans.json"
_ID = re.compile(r"^\d{7}$")


def parse_courses(html: str) -> list[dict]:
    """All courses found in the page's tables, one entry per course number."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or not _ID.match(cells[0]):
            continue
        name = cells[1] if len(cells) > 1 else ""
        credits = next((int(c) for c in cells[2:] if re.fullmatch(r"\d", c)), None)
        found.setdefault(cells[0], {"course_id": cells[0], "name": name, "credits": credits})
    return list(found.values())


def parse_total_credits(html: str) -> int | None:
    """The total credit hours the page states (e.g. '132 Cr. Hrs.'), if it can be found."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for pattern in (r"[Tt]otal[^0-9]{0,40}(\d{3})\s*(?:Cr|credit)", r"(\d{3})\s*(?:Cr\.?\s*Hr|[Cc]redit [Hh]ours)"):
        m = re.search(pattern, text)
        if m and 100 <= int(m.group(1)) <= 160:
            return int(m.group(1))
    return None


def main() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"programs": {}}
    data["source"] = "University of Sharjah degree pages (study plan tables)"
    data["retrieved"] = date.today().isoformat()
    for name, url in PAGES.items():
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"{name}: could not fetch ({exc})")
            continue
        courses = parse_courses(r.text)
        total = parse_total_credits(r.text)
        plan = data["programs"].setdefault(name, {"match_names": [name]})
        plan.update(url=url, courses=courses)
        if total:
            plan["total_credits"] = total
        print(f"{name}: {len(courses)} courses, total credits {plan.get('total_credits')}")
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {OUT}")


if __name__ == "__main__":
    sys.exit(main())
