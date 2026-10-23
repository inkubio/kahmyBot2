"""
Statistics for KähmyBot2.0

Keeps a JSON log of every kähmy chain and comment so the bot remembers past kähmys,
computes the grill temperature from that log and builds the daily recap.
"""

import html
import io
import json
import math
import os
import threading
from collections import Counter
from datetime import datetime, timedelta
from enum import IntEnum
from zoneinfo import ZoneInfo

import requests

import config


class KAHMY_ID(IntEnum):
    HALLITUS = 5
    TOIMARI = 6


TZ = ZoneInfo("Europe/Helsinki")
STATS_FILE = getattr(
    config,
    "stats_file",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "kahmy_stats.json"),
)

# Temperature rules: a fire heating a small room
MAX_TEMP = 3000
# Calibrated against the forum's history 2024-2026: only the record toimari deadline day
# (65 chains in 24 h) reaches 2000, a busy hallitus discussion day reaches about 1500
LOGS = {"chain": 3, "comment": 1}
# Each log closes this share of the gap to MAX_TEMP: a comment heats a cold room by 40 degrees
LOG_KEEP = 1 - 40 / MAX_TEMP
# Cooling per hour, chosen so that a comment every 4 hours keeps the room at 200 degrees at its coldest
COOLING_RATE = math.log((200 + (MAX_TEMP - 200) * (1 - LOG_KEEP)) / 200) / 4
SAMPLE_INTERVAL = timedelta(minutes=15)
WEEKDAYS = [
    "maanantai",
    "tiistai",
    "keskiviikko",
    "torstai",
    "perjantai",
    "lauantai",
    "sunnuntai",
]
# Checked from highest to lowest, only the highest crossed threshold is announced.
# From a cold room these are crossed by roughly 1 chain + 1 comment within 1 h,
# 2 chains + 3 comments within 3 h and 3 chains + 5 comments within 5 h
THRESHOLDS = [
    (450, lambda ts: "Liekkeihn bensaa sataa"),
    (300, lambda ts: f"Ilmeisesti grilli{WEEKDAYS[ts.weekday()]}"),
    (150, lambda ts: "Grilli lämpenee"),
]

_lock = threading.Lock()


def _load():
    if not os.path.exists(STATS_FILE):
        return {"events": [], "topic_authors": {}, "last_recap": None}
    with open(STATS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(state):
    """Write atomically so a crash mid-write never corrupts the history"""
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATS_FILE)


def _ts(event):
    return datetime.fromisoformat(event["ts"])


def _topic_url(event):
    return f"{config.forum_url.rstrip('/')}/t/{event['topic_slug']}/{event['topic_id']}"


def _fetch_topic_author(topic_id):
    """Ask Discourse who created a topic that was started before the bot kept a log"""
    headers = {}
    if hasattr(config, "discourse_api_key"):
        headers = {
            "Api-Key": config.discourse_api_key,
            "Api-Username": getattr(config, "discourse_api_username", "system"),
        }
    try:
        response = requests.get(
            f"{config.forum_url.rstrip('/')}/t/{topic_id}.json",
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        return response.json()["details"]["created_by"]["username"]
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"Could not fetch author of topic {topic_id}: {e}")
        return None


def add_logs(temp, logs):
    """Every log heats the room less the closer it already is to MAX_TEMP"""
    return MAX_TEMP - (MAX_TEMP - temp) * LOG_KEEP**logs


def cool(temp, hours):
    """Newton's law of cooling, never goes below zero"""
    return temp * math.exp(-COOLING_RATE * hours)


def temperature_at(events, t):
    """
    Replay the (time sorted) events up to time 't'.
    Every event adds logs to the fire, between events the room cools down.
    """
    temp = 0.0
    last = None
    for event in events:
        ts = _ts(event)
        if ts > t:
            break
        if last is not None:
            temp = cool(temp, (ts - last).total_seconds() / 3600)
        temp = add_logs(temp, LOGS[event["type"]])
        last = ts
    if last is not None:
        temp = cool(temp, (t - last).total_seconds() / 3600)
    return temp


def record_post(payload):
    """
    Store a post from the Discourse webhook payload.
    Returns a grill message if this post pushed the temperature over a threshold, otherwise None.
    """
    created_at = payload.get("created_at")
    ts = (
        datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(TZ)
        if created_at
        else datetime.now(TZ)
    )
    topic_id = str(payload["topic_id"])
    is_chain = payload["post_number"] == 1

    with _lock:
        state = _load()

        # Discourse retries webhooks, so the same post may arrive more than once
        if any(e["post_id"] == payload["id"] for e in state["events"]):
            return None

        kind = None
        if is_chain:
            state["topic_authors"][topic_id] = payload["username"]
        else:
            author = state["topic_authors"].get(topic_id)
            if author is None:
                author = _fetch_topic_author(topic_id)
                if author is not None:
                    state["topic_authors"][topic_id] = author
            if author is not None:
                kind = "answer" if payload["username"] == author else "question"

        event = {
            "post_id": payload["id"],
            "type": "chain" if is_chain else "comment",
            "topic_id": payload["topic_id"],
            "topic_slug": payload["topic_slug"],
            "topic_title": payload["topic_title"],
            "category_id": payload["category_id"],
            "username": payload["username"],
            "name": payload["name"],
            "kind": kind,
            "ts": ts.isoformat(),
        }

        before = temperature_at(state["events"], ts)
        state["events"].append(event)
        state["events"].sort(key=_ts)
        _save(state)

    after = add_logs(before, LOGS[event["type"]])
    for threshold, message in THRESHOLDS:
        if before <= threshold < after:
            return message(ts)
    return None


def _rolling_average(values, window=3):
    """Centered rolling average, the edges average over the points available"""
    half = window // 2
    result = []
    for i in range(len(values)):
        chunk = values[max(0, i - half) : i + half + 1]
        result.append(sum(chunk) / len(chunk))
    return result


def plot_temperature(events, start, end):
    """Plot the smoothed temperature between 'start' and 'end', returns PNG bytes"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    times = []
    t = start
    while t <= end:
        times.append(t)
        t += SAMPLE_INTERVAL
    # Posts made after the last full interval would otherwise be missing from the graph
    if times[-1] < end:
        times.append(end)
    temps =_rolling_average([temperature_at(events, t) for t in times])

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(times, temps, color="tab:red", linewidth=2)
    ax.fill_between(times, temps, color="tab:red", alpha=0.15)
    # Only show the thresholds near the curve so a quiet day isn't squashed flat
    for threshold, _ in THRESHOLDS:
        if threshold < 1.2 * max(temps):
            ax.axhline(threshold, color="grey", linestyle="--", linewidth=0.8)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("°C")
    ax.set_title("Grillin lämpötila")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
    ax.grid(alpha=0.3)
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=120)
    plt.close(fig)
    return buffer.getvalue()


def build_recap(now=None):
    """
    Build the recap of everything since the previous recap.
    Returns (text, png) or None if nothing happened. Marks the recap as done either way.
    """
    now = now or datetime.now(TZ)

    with _lock:
        state = _load()
        start = (
            datetime.fromisoformat(state["last_recap"])
            if state.get("last_recap")
            else now - timedelta(days=1)
        )
        state["last_recap"] = now.isoformat()
        _save(state)

    events = state["events"]
    window = [e for e in events if start < _ts(e) <= now]
    if not window:
        return None

    chains = [e for e in window if e["type"] == "chain"]
    comments = [e for e in window if e["type"] == "comment"]
    questions = sum(1 for e in comments if e["kind"] == "question")
    answers = sum(1 for e in comments if e["kind"] == "answer")

    names = {e["username"]: e["name"] or e["username"] for e in comments}
    top_commenters = Counter(e["username"] for e in comments).most_common(3)
    topics = {e["topic_id"]: e for e in comments}
    top_chains = Counter(e["topic_id"] for e in comments).most_common(3)

    max_temp = max(
        [temperature_at(events, start)] + [temperature_at(events, _ts(e)) for e in window]
    )

    year = [e for e in events if _ts(e).year == now.year]

    def year_count(category_id, event_type):
        return sum(
            1 for e in year if e["category_id"] == category_id and e["type"] == event_type
        )

    lines = [
        "<b>Päivän kähmykatsaus</b>",
        f"Uusia kähmyjä: <b>{len(chains)}</b>",
        f"Kommentteja kähmyihin: <b>{len(comments)}</b> joista {questions} kysymystä ja {answers} vastausta",
    ]
    lines += [
        f"\nGrilli oli tänään kuumimmillaan: <b>{max_temp:.0f}°C</b>",
        f"\n<b>Vuonna {now.year} yhteensä</b>",
        f"Hallituskähmyt: {year_count(KAHMY_ID.HALLITUS, 'chain')} kähmyä, "
        f"{year_count(KAHMY_ID.HALLITUS, 'comment')} kommenttia",
        f"Toimarikähmyt: {year_count(KAHMY_ID.TOIMARI, 'chain')} kähmyä, "
        f"{year_count(KAHMY_ID.TOIMARI, 'comment')} kommenttia",
    ]

    return "\n".join(lines), plot_temperature(events, start, now)
