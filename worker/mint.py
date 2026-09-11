#!/usr/bin/env python3
"""Issue a one-shot button and print the message a chat platform can post.

    docker compose exec worker python3 /app/mint.py \
        --action restart-container --arg container=web --label "Restart web"

The token it prints is the only thing that makes a click mean anything, and it
is spent the moment it is used. It is minted HERE, in the privileged container,
and never by the edge: a component that can issue its own permissions has not
been fenced off from anything.
"""
import argparse
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402

ACTION_URL = os.environ.get("ACTION_URL", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True)
    ap.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--label", default="")
    ap.add_argument("--group", default="",
                    help="tokens sharing a group are all spent by one click")
    ap.add_argument("--json", action="store_true",
                    help="print the token only, for scripts and tests")
    args = ap.parse_args()

    values = {}
    for pair in args.arg:
        key, _, val = pair.partition("=")
        values[key] = val

    token = secrets.token_urlsafe(24)
    entry = {"action": args.action, "args": values,
             "label": args.label or args.action,
             "group": args.group or token, "issued": time.time()}
    store.update(lambda tokens: tokens.update({token: entry}))

    if args.json:
        print(json.dumps({"token": token}))
        return
    if not ACTION_URL:
        print("ACTION_URL is not set, so the button below has nowhere to go.",
              file=sys.stderr)
    print(json.dumps({"attachments": [{
        "title": entry["label"],
        "actions": [{"name": entry["label"], "integration": {
            "url": ACTION_URL, "context": {"token": token}}}]}]}, indent=2))


if __name__ == "__main__":
    main()
