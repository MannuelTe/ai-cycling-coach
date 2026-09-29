"""Coach mode: the athletes you coach on intervals.icu, and adding one to the roster.

Usage:
    python3 scripts/roster.py                         # everyone your key can see, with form
    python3 scripts/roster.py add <name> <athlete_id> # new folder athletes/<name>/ from templates

An athlete shows up once they've accepted you as coach (or follower) on intervals.icu. Followers
are read-only: writes to their calendar fail with 403. After adding, work on one athlete with
    ATHLETE=<name> python3 scripts/check_plan.py
"""
import re, sys, shutil
from intervals_common import api_get, read_roster, ROOT, ROSTER

TEMPLATES = ["ATHLETE.md", "SEASON_PLAN.md", "CHECKINS.md", "DECISIONS.md", "RESEARCH_LOG.md"]


def is_blank_profile(text):
    """True if an ATHLETE.md is still the empty template (no name filled in)."""
    return bool(re.search(r"^- Name / location:\s*$", text, re.M))


def list_athletes():
    rows = api_get("/athlete/0/athlete-summary.json") or []
    latest = {}
    for r in rows:  # one row per athlete per week; keep the most recent
        if r["athlete_id"] not in latest or r["date"] > latest[r["athlete_id"]]["date"]:
            latest[r["athlete_id"]] = r
    names = {aid: name for name, aid in read_roster().items()}
    print(f"{'roster':12} {'athlete_id':12} {'CTL':>4} {'Form':>5}  name")
    for aid, r in sorted(latest.items(), key=lambda kv: kv[1].get("athlete_name") or ""):
        print(f"{names.get(aid, '-'):12} {aid:12} {r.get('fitness') or 0:4.0f} "
              f"{(r.get('form') or 0):+5.0f}  {r.get('athlete_name')}")


def add(name, athlete_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise SystemExit("Name: lowercase letters, digits, - or _ (it becomes a folder name).")
    if name in read_roster():
        raise SystemExit(f"{name} is already in the roster.")
    templates = ROOT / "athlete"
    if not is_blank_profile((templates / "ATHLETE.md").read_text()):
        raise SystemExit("athlete/ holds a real profile, not the blank templates. Move it into "
                         "athletes/<your name>/ first; `git checkout athlete/` restores the templates.")
    profile = api_get(f"/athlete/{athlete_id}")  # fails here if your key can't see them
    folder = ROOT / "athletes" / name
    folder.mkdir(parents=True)
    for f in TEMPLATES:
        shutil.copy(templates / f, folder / f)
    if not ROSTER.exists():
        ROSTER.write_text("name,athlete_id\n")
    with ROSTER.open("a") as f:
        f.write(f"{name},{athlete_id}\n")
    print(f"Added {name} ({profile.get('name')}). Onboard them with ATHLETE={name}.")


if __name__ == "__main__":
    if sys.argv[1:2] == ["add"] and len(sys.argv) == 4:
        add(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 1:
        list_athletes()
    else:
        raise SystemExit(__doc__)
