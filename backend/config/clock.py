"""Every time written for a person to read is on the 12-hour clock: "9:30 AM",
never "09:30" - the owner's request, 29 Sep 2026.

Only prose goes through here: a refusal, the assistant's answer, a text. A
time the API hands a screen as DATA ("start": "09:30" on a slot, a time
field) stays 24-hour, because it is sent back when booking and compared as a
string; the screens write it out with clock() in frontend/src/utils/time.js.
"""


def clock(t):
    """"9:30 AM" from a time or a (local) datetime. Built by hand: %-I is a
    glibc extension that raises on Windows, where the local copy runs, and
    plain %I pads the hour with a zero."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"
