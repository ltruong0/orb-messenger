"""Moods an orb can show (decisions 48). Keys match contracts/schemas/inbox.schema.json.

The orb draws each face itself; the emoji is only for the phone UI and history.
"""

from __future__ import annotations

MOODS: list[dict[str, str]] = [
    {"key": "happy", "label": "Happy", "emoji": "😊"},
    {"key": "sad", "label": "Sad", "emoji": "😢"},
    {"key": "confused", "label": "Confused", "emoji": "😕"},
    {"key": "cold", "label": "Cold", "emoji": "🥶"},
    {"key": "hot", "label": "Hot", "emoji": "🥵"},
    {"key": "scared", "label": "Scared", "emoji": "😱"},
    {"key": "surprised", "label": "Surprised", "emoji": "😮"},
    {"key": "angry", "label": "Angry", "emoji": "😠"},
    {"key": "sleepy", "label": "Sleepy", "emoji": "😴"},
    {"key": "love", "label": "In love", "emoji": "😍"},
    {"key": "laughing", "label": "Laughing", "emoji": "😂"},
    {"key": "cool", "label": "Cool", "emoji": "😎"},
    {"key": "sick", "label": "Sick", "emoji": "🤢"},
    {"key": "excited", "label": "Excited", "emoji": "🤩"},
]
MOOD_KEYS = {m["key"] for m in MOODS}
