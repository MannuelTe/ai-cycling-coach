"""Shared helpers for talking to the intervals.icu API.

Credentials come from the environment or a .env file in the repo root (see .env.example).
Never hard-code them. Get your key + athlete ID at intervals.icu -> Settings -> Developer.

Coach mode: with several athletes in athletes/roster.csv, set ATHLETE=<name> to pick one.
The API key is always the coach's own; intervals.icu lets it act on athletes who accepted
you as their coach. Without ATHLETE, everything works on athlete/ and INTERVALS_ATHLETE_ID.

Failed requests are appended to logs/errors.log (no key, athlete IDs redacted).
"""
import os, csv, json, base64, datetime, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://intervals.icu/api/v1"
ERROR_LOG = ROOT / "logs" / "errors.log"
ROSTER = ROOT / "athletes" / "roster.csv"


def _load_dotenv():
    """Minimal .env loader (no external deps). Only sets keys not already in env."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def has_credentials():
    return bool(os.environ.get("INTERVALS_API_KEY")
                and (os.environ.get("ATHLETE") or os.environ.get("INTERVALS_ATHLETE_ID")))


def read_roster(path=None):
    """Coach mode: {name: athlete_id} from athletes/roster.csv. Empty if there is no roster."""
    path = Path(path or ROSTER)
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["name"].strip(): r["athlete_id"].strip() for r in csv.DictReader(f) if r.get("name")}


def _selected():
    """The ATHLETE name, checked against the roster. None in single-athlete mode."""
    name = os.environ.get("ATHLETE")
    if name and name not in read_roster():
        raise SystemExit(f"ATHLETE={name} is not in athletes/roster.csv. "
                         "Add them with: python3 scripts/roster.py add <name> <athlete_id>")
    return name


def athlete_dir():
    """Folder holding the current athlete's files: athletes/<name>/ in coach mode, else athlete/."""
    name = _selected()
    return ROOT / "athletes" / name if name else ROOT / "athlete"


def athlete_id():
    name = _selected()
    if name:
        return read_roster()[name]
    aid = os.environ.get("INTERVALS_ATHLETE_ID")
    if not aid:
        raise SystemExit("INTERVALS_ATHLETE_ID not set. Add it to .env (see .env.example).")
    return aid


def athlete_url():
    return f"{API}/athlete/{athlete_id()}"


def get_key():
    key = os.environ.get("INTERVALS_API_KEY")
    if not key:
        raise SystemExit("INTERVALS_API_KEY not set. Add it to .env (see .env.example).")
    return key


def _headers():
    auth = base64.b64encode(f"API_KEY:{get_key()}".encode()).decode()
    # intervals.icu returns 403 to the default Python-urllib user-agent,
    # so we must send an explicit User-Agent.
    return {
        "Authorization": "Basic " + auth,
        "Content-Type": "application/json",
        "User-Agent": "ai-cycling-coach/1.0",
    }


def _redact(text):
    """Strip the API key and every known athlete ID from anything we write to disk."""
    text = str(text)
    ids = [os.environ.get("INTERVALS_ATHLETE_ID"), *read_roster().values()]
    for secret, label in ((os.environ.get("INTERVALS_API_KEY"), "{key}"),
                          *((aid, "{athlete}") for aid in ids)):
        if secret:
            text = text.replace(secret, label)
    return text


def log_error(source, message):
    """Append one line to logs/errors.log. Also used by the agent for non-HTTP problems."""
    ERROR_LOG.parent.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = " ".join(_redact(f"{stamp} [{source}] {message}").split())
    with ERROR_LOG.open("a") as f:
        f.write(line + "\n")


def _call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    path = url.replace(API, "")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        log_error("http", f"{method} {path} -> {e.code} {detail}")
        raise
    except urllib.error.URLError as e:
        log_error("network", f"{method} {path} -> {e.reason}")
        raise
    return json.loads(text) if text else None


def api_get(path):
    """GET a non-athlete-scoped endpoint, e.g. '/activity/{id}/streams?types=watts'."""
    return _call("GET", API + path)


def athlete_get(subpath):
    """GET any athlete-scoped endpoint, e.g. '/activities?oldest=...&newest=...'."""
    return _call("GET", athlete_url() + subpath)


def request(method, path="", body=None):
    """Calendar events endpoint. path is '' (list/create) or '/{event_id}'."""
    return _call(method, athlete_url() + "/events" + path, body)


def _workout_body(date, name, note, steps, sport):
    return {
        "category": "WORKOUT",
        "start_date_local": date + "T00:00:00",
        "type": sport,
        "name": name,
        "description": note + "\n\n" + steps,
    }


def create_workout(date, name, note, steps, sport="Ride"):
    return request("POST", body=_workout_body(date, name, note, steps, sport))


def update_workout(event_id, date, name, note, steps, sport="Ride"):
    return request("PUT", f"/{event_id}", body=_workout_body(date, name, note, steps, sport))


def blank_steps(event):
    """Steps of a parsed workout that ended up without a power target (the step-syntax gotcha).

    Walks nested repeat blocks. An empty list means every step has a target.
    """
    bad = []

    def walk(steps):
        for s in steps or []:
            if s.get("steps"):
                walk(s["steps"])
            elif not s.get("power"):
                bad.append(s)

    walk((event.get("workout_doc") or {}).get("steps"))
    return bad
