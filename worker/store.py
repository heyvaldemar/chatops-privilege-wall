#!/usr/bin/env python3
"""The token store, shared by the worker and by mint.py.

Two processes write this file: the worker burns tokens as they are used, and
mint.py adds them. Read-modify-write from both without a lock loses whichever
one finishes second, which here means a button that was issued and then quietly
was not. So every change runs under flock and the file is replaced atomically.
"""
import contextlib
import fcntl
import json
import os

STATE = os.environ.get("STATE_FILE", "/state/tokens.json")
LOCK = STATE + ".lock"


@contextlib.contextmanager
def locked():
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    fh = open(LOCK, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def read():
    try:
        with open(STATE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write(tokens):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(tokens, fh)
    os.replace(tmp, STATE)


def update(fn):
    """Apply fn to the stored tokens under the lock. Returns what fn returns."""
    with locked():
        tokens = read()
        result = fn(tokens)
        write(tokens)
        return result
