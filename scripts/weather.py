"""Weather, daylight and setup for the days ahead (read-only). Used by check_plan.py.

Where the athlete is, what they can ride there, and when they work come from
athlete/locations.csv (see templates/athlete/locations.example.csv):

    from,to,place,lat,lon,trainer,road,gravel,work_days,work_hours
    2026-10-23,2026-10-30,Zurich,47.37,8.54,no,yes,no,Mon-Fri,08:30-17:30

Forecasts come from Open-Meteo (free, no key, ~16 days ahead).

Usage:
    python3 scripts/weather.py               # next 7 days
    python3 scripts/weather.py 2026-11-02 10 # 10 days from this date
"""
import sys, csv, json, datetime, urllib.request, urllib.parse
from pathlib import Path
from intervals_common import ROOT, log_error

LOCATIONS_FILE = ROOT / "athlete" / "locations.csv"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"

# Flag thresholds for outdoor sessions.
RAIN_PROB = 60      # % chance
RAIN_MM = 2.0       # mm over the day
COLD_MIN_C = 3      # daily low below this: ice risk on early/late rides
GUST_KMH = 45

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EQUIPMENT = ("trainer", "road", "gravel")


def load_locations(path=LOCATIONS_FILE):
    """Rows of locations.csv with dates parsed. [] if the file is missing."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            if not r.get("from"):
                continue
            r["from"] = datetime.date.fromisoformat(r["from"])
            r["to"] = datetime.date.fromisoformat(r["to"])
            rows.append(r)
    return rows


def location_on(day, locations):
    """The row whose from..to range (inclusive) covers this date, or None."""
    for r in locations:
        if r["from"] <= day <= r["to"]:
            return r
    return None


def available(loc):
    return [k for k in EQUIPMENT if (loc.get(k) or "").strip().lower() in ("yes", "y", "1", "true")]


def parse_work_days(text):
    """'Mon-Fri' or 'Mon,Tue,Thu' -> set of weekday numbers (Mon=0). Blank -> None (unknown)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() == "none":
        return set()
    out = set()
    for part in text.split(","):
        if "-" in part:
            a, b = (DAYS.index(p.strip()[:3].title()) for p in part.split("-"))
            out.update(range(a, b + 1))
        else:
            out.add(DAYS.index(part.strip()[:3].title()))
    return out


def parse_work_hours(text):
    """'08:30-17:30' -> (time, time). Blank -> None (unknown)."""
    text = (text or "").strip()
    if not text:
        return None
    a, b = text.split("-")
    return datetime.time.fromisoformat(a.strip()), datetime.time.fromisoformat(b.strip())


def daylight_windows(day, sunrise, sunset, loc):
    """Free daylight in minutes: [(label, minutes), ...].

    On a work day that's before work and after work. Otherwise the whole day.
    Returns None if work days or hours are unknown, so the agent knows to ask.
    """
    work_days = parse_work_days(loc.get("work_days"))
    if work_days is None:
        return None
    if day.weekday() not in work_days:
        return [("all day", (sunset - sunrise).seconds // 60)]
    hours = parse_work_hours(loc.get("work_hours"))
    if hours is None:
        return None
    start = datetime.datetime.combine(day, hours[0])
    end = datetime.datetime.combine(day, hours[1])
    before = max(0, (start - sunrise).total_seconds() // 60)
    after = max(0, (sunset - end).total_seconds() // 60)
    return [("before work", int(before)), ("after work", int(after))]


def flags(w, planned_mins=None, windows=None):
    """Warnings for an outdoor ride on this day. w is one day of forecast."""
    out = []
    if (w.get("rain_prob") or 0) >= RAIN_PROB or (w.get("rain_mm") or 0) >= RAIN_MM:
        out.append("WET")
    if w.get("t_min") is not None and w["t_min"] < COLD_MIN_C:
        out.append("ICE RISK")
    if (w.get("gusts") or 0) >= GUST_KMH:
        out.append("WINDY")
    if planned_mins and windows is not None and max(m for _, m in windows) < planned_mins:
        out.append("DARK: lights or trainer")
    return out


def forecast(lat, lon, start, end):
    """Daily forecast {date: {...}} from Open-Meteo, in local time at that place."""
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,"
                 "sunrise,sunset",
    })
    req = urllib.request.Request(f"{FORECAST_API}?{q}", headers={"User-Agent": "ai-cycling-coach/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())["daily"]
    out = {}
    for i, ds in enumerate(d["time"]):
        out[datetime.date.fromisoformat(ds)] = {
            "t_min": d["temperature_2m_min"][i], "t_max": d["temperature_2m_max"][i],
            "rain_mm": d["precipitation_sum"][i], "rain_prob": d["precipitation_probability_max"][i],
            "wind": d["wind_speed_10m_max"][i], "gusts": d["wind_gusts_10m_max"][i],
            "sunrise": datetime.datetime.fromisoformat(d["sunrise"][i]),
            "sunset": datetime.datetime.fromisoformat(d["sunset"][i]),
        }
    return out


def days_ahead(start, end, locations=None):
    """{date: (loc, weather)} for start..end. loc or weather may be None.

    Fetches once per location. A failed fetch is logged and that location's
    weather is left as None, so check_plan.py keeps running.
    """
    locations = load_locations() if locations is None else locations
    out, by_place = {}, {}
    d = start
    while d <= end:
        loc = location_on(d, locations)
        out[d] = (loc, None)
        if loc:
            by_place.setdefault((loc["lat"], loc["lon"]), []).append(d)
        d += datetime.timedelta(days=1)
    for (lat, lon), dates in by_place.items():
        try:
            fc = forecast(lat, lon, min(dates), max(dates))
        except Exception as e:  # network, bad coordinates, date beyond the forecast range
            log_error("weather", f"forecast {lat},{lon} {min(dates)}..{max(dates)} -> {e}")
            continue
        for d in dates:
            out[d] = (out[d][0], fc.get(d))
    return out


def describe(day, loc, w, planned_mins=None, indoor=False):
    """One line for check_plan.py: weather, daylight, what's available, and flags."""
    if loc is None:
        return "no location for this date in athlete/locations.csv: ask where they'll be and what they can ride"
    parts = [loc["place"]]
    if w is None:
        parts.append("weather unavailable")
    else:
        parts.append(f"{w['t_min']:.0f}–{w['t_max']:.0f}°C")
        parts.append(f"rain {w['rain_prob'] or 0}%/{w['rain_mm'] or 0:.1f}mm")
        parts.append(f"wind {w['wind']:.0f} gusts {w['gusts']:.0f}")
        parts.append(f"light {w['sunrise']:%H:%M}–{w['sunset']:%H:%M}")
    avail = available(loc)
    parts.append("avail: " + (", ".join(avail) if avail else "nothing listed"))
    windows = None
    if w is not None:
        windows = daylight_windows(day, w["sunrise"], w["sunset"], loc)
        if windows is None:
            parts.append("work schedule unknown: ask when they work")
        elif len(windows) > 1:
            parts.append("daylight " + " / ".join(f"{m}' {label}" for label, m in windows))
        if not indoor:
            warn = flags(w, planned_mins, windows)
            if warn:
                parts.append("** " + ", ".join(warn) + " **")
    return "  ·  ".join(parts)


def main():
    start = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date.today()
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    locations = load_locations()
    if not locations:
        print(f"No {LOCATIONS_FILE.relative_to(ROOT)}. Copy templates/athlete/locations.example.csv and fill it in.")
        return
    for d, (loc, w) in days_ahead(start, start + datetime.timedelta(days=n - 1), locations).items():
        print(f"  {d} {DAYS[d.weekday()]}  {describe(d, loc, w)}")


if __name__ == "__main__":
    main()
