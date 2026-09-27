"""ASCII-рендер таймлайна.

Про ширину символов:
  * выводим только символы гарантированно одной колонки: ASCII +
    Unicode Box Drawing (U+2500–U+257F);
  * имена задач проходят через sanitize(): широкие и комбинирующиеся
    символы заменяются/удаляются, так что после неё display_width == len;
  * вся раскладка идёт через fit()/display_width(), а не через len();
  * ascii_only=True — аварийный режим для капризных терминалов.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from models import ScheduledTask, Task


# ---------------------------------------------------------------------------
# ширина символов
# ---------------------------------------------------------------------------

def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s: str) -> int:
    return sum(_char_width(c) for c in s)


def fit(s: str, width: int) -> str:
    if width <= 0:
        return ""
    out, used = [], 0
    for ch in s:
        cw = _char_width(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out) + " " * (width - used)


def sanitize(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\t":
            out.append(" ")
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            out.append("?")
            continue
        out.append(ch)
    return "".join(out)


def _center(name: str, width: int) -> str:
    name = sanitize(name)
    if display_width(name) > width:
        name = fit(name, width)
    nw = len(name)
    left = (width - nw) // 2
    right = width - nw - left
    return " " * left + name + " " * right


# ---------------------------------------------------------------------------
# собственно рендер
# ---------------------------------------------------------------------------

_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "─", "v": "│", "tick": "┬"}

_ASCII_FALLBACK = str.maketrans({
    "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "─": "-", "│": "|", "┬": "+",
})


def _assign_tracks(schedule: dict[int, ScheduledTask]) -> list[list[int]]:
    order = sorted(schedule, key=lambda i: (schedule[i].start, schedule[i].end))
    tracks: list[list[int]] = []
    for i in order:
        s = schedule[i]
        for tr in tracks:
            if all(schedule[j].end <= s.start or s.end <= schedule[j].start
                   for j in tr):
                tr.append(i)
                break
        else:
            tracks.append([i])
    return tracks


def timeline(tasks: dict[int, Task],
             schedule: dict[int, ScheduledTask],
             minutes_per_char: int = 10,
             gutter_width: int = 4,
             ascii_only: bool = False) -> str:
    if not schedule:
        return "(nothing scheduled yet)"

    t0 = min(s.start for s in schedule.values())
    t1 = max(s.end   for s in schedule.values())
    total_min  = max(1, int((t1 - t0).total_seconds() // 60))
    total_cols = max(4, (total_min + minutes_per_char - 1) // minutes_per_char)

    def col(dt: datetime) -> int:
        return int((dt - t0).total_seconds() // 60) // minutes_per_char

    tracks = _assign_tracks(schedule)
    gutter = " " * gutter_width

    ruler_top = [" "] * total_cols
    ruler_mid = [_BOX["h"]] * total_cols

    cur = t0.replace(minute=0, second=0, microsecond=0)
    if cur < t0:
        cur += timedelta(hours=1)
    while cur <= t1:
        c = col(cur)
        if 0 <= c < total_cols:
            ruler_mid[c] = _BOX["tick"]
            label = cur.strftime("%H")
            start_pos = max(0, min(c - len(label) // 2, total_cols - len(label)))
            for k, ch in enumerate(label):
                pos = start_pos + k
                if 0 <= pos < total_cols:
                    ruler_top[pos] = ch
        cur += timedelta(hours=1)

    lines = [
        gutter + "".join(ruler_top).rstrip(),
        gutter + "".join(ruler_mid).rstrip(),
    ]

    for tidx, tr in enumerate(tracks, start=1):
        label = fit(f"T{tidx}", gutter_width - 1)
        top_row = [" "] * total_cols
        mid_row = [" "] * total_cols
        bot_row = [" "] * total_cols

        for i in sorted(tr, key=lambda i: schedule[i].start):
            s = schedule[i]
            c0 = col(s.start)
            c1 = col(s.end)
            w = max(3, c1 - c0)
            if c0 + w > total_cols:
                w = total_cols - c0
            if w < 3:
                continue

            top_row[c0]         = _BOX["tl"]
            top_row[c0 + w - 1] = _BOX["tr"]
            bot_row[c0]         = _BOX["bl"]
            bot_row[c0 + w - 1] = _BOX["br"]
            for k in range(1, w - 1):
                top_row[c0 + k] = _BOX["h"]
                bot_row[c0 + k] = _BOX["h"]
            mid_row[c0]         = _BOX["v"]
            mid_row[c0 + w - 1] = _BOX["v"]

            name = _center(tasks[i].name, w - 2)
            for k, ch in enumerate(name):
                mid_row[c0 + 1 + k] = ch

        lines.append(label + " " + "".join(top_row).rstrip())
        lines.append(label + " " + "".join(mid_row).rstrip())
        lines.append(label + " " + "".join(bot_row).rstrip())

    out = "\n".join(lines)
    if ascii_only:
        out = out.translate(_ASCII_FALLBACK)
    return out