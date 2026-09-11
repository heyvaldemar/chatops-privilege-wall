#!/usr/bin/env python3
"""The privileged half. It reads the queue, judges, acts, and answers.

It holds whatever privilege the action needs, so it holds no way in: nothing is
published, it listens on no port, and it is not on the network the edge is on.
The only thing that reaches it is a file in the queue directory, written by a
process that can do nothing else. See edge/edge.py for why.

Everything that matters is decided here. Whether the token is real, whether the
caller is allowed, what the action means: all of it happens in the process that
already had to be trusted, never in the one exposed to the internet.

The action table itself is actions.py, which is the file you edit.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions  # noqa: E402
import store  # noqa: E402

QUEUE = os.environ.get("QUEUE_DIR", "/queue")
HEARTBEAT = os.environ.get("HEARTBEAT_FILE", "/tmp/HEARTBEAT")
NOTIFY_URL = os.environ.get("NOTIFY_URL", "")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "86400"))
# Replies nobody collected: the listener gave up waiting, or the click came from
# something that never read the answer.
REPLY_TTL = int(os.environ.get("REPLY_TTL", "600"))

# WHO MAY ACT. Empty means nobody, and that is enforced below rather than
# implied. See the check in decide() for why this distinction is the whole
# difference between a closed door and an open one.
ALLOWED = {v.strip() for v in os.environ.get("ALLOWED_USER_IDS", "").split(",")
           if v.strip()}


def now():
    return time.time()


def expire_in_place(tokens):
    cutoff = now() - TOKEN_TTL
    for key in [k for k, v in tokens.items() if v.get("issued", 0) <= cutoff]:
        tokens.pop(key, None)


def spend(token):
    """Take a token and every token issued with it, in one locked step.

    Returns (what it was for, everything taken) so a refusal can put it all
    back. Doing this as read-then-write without the lock is how one message gets
    actioned twice by two clicks a moment apart.
    """
    def apply(tokens):
        expire_in_place(tokens)
        pending = tokens.get(token)
        if not pending:
            return None, {}
        group = pending.get("group", token)
        burned = {k: v for k, v in tokens.items() if v.get("group", k) == group}
        for key in burned:
            tokens.pop(key, None)
        return pending, burned
    return store.update(apply)


def give_back(burned):
    def apply(tokens):
        expire_in_place(tokens)
        tokens.update(burned)
    store.update(apply)


def notify(title, text, color="#d9534f"):
    """Say it where it can actually be seen.

    A reply carried in the HTTP response is not a reliable record. It can be
    dropped by the chat platform, it is visible to one person, and this process
    cannot read it back to check. Anything a person must know goes to the same
    place the button itself came from, so if the button was visible, this is.
    """
    if not NOTIFY_URL:
        return
    payload = json.dumps(
        {"attachments": [{"color": color, "title": title, "text": text}]}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as exc:
        print("notify failed: %s" % exc, flush=True)


def decide(req):
    """Judge one request and carry it out. Returns the reply body."""
    token = req.get("token", "")
    who = req.get("user_name") or req.get("user_id") or "?"
    uid = req.get("user_id") or ""

    # FAIL CLOSED. Written as `if ALLOWED and uid not in ALLOWED` this reads
    # correctly and does the opposite: an EMPTY list stops meaning "nobody" and
    # starts meaning "skip the check", so anybody holding a token may act. That
    # is not a hypothetical. It shipped that way in the system this template
    # comes from, the list was empty the whole time, and nothing had gone wrong
    # only because nobody had tried. A setting whose blank value switches off
    # the thing it configures is a trap for whoever tidies the file next.
    if not ALLOWED:
        print("refused: ALLOWED_USER_IDS is empty, so nobody can act", flush=True)
        return {"ephemeral_text": "No operators are configured, so nothing was done."}

    if uid not in ALLOWED:
        # THE REFUSAL NAMES THE ID, not just the person. An id is not a secret;
        # every id this checks against sits in the configuration in the clear.
        # Naming only the human tells you who was refused and not the one value
        # you need to fix it, which costs a second click on a button that should
        # have worked the first time.
        print("refused: %s (id %s) is not in ALLOWED_USER_IDS"
              % (who, uid or "MISSING"), flush=True)
        # ANNOUNCED, not silent. Silence is what lets a stale allowlist survive:
        # everyone clicks, everyone is refused, nobody sees anything, and the
        # list stays broken for as long as the buttons have existed.
        notify(":lock: A click was refused",
               "%s is not on the operator list, so nothing was done. Their id is "
               "`%s`. Add it to ALLOWED_USER_IDS if they should be on it."
               % (who, uid or "not supplied"), "#f0ad4e")
        return {"ephemeral_text":
                "You are not allowed to do this. Your id is `%s` and it is not "
                "on the operator list." % (uid or "not supplied")}

    pending, burned = spend(token)
    if not pending:
        print("stale click by %s on a token that is gone" % who, flush=True)
        notify(":clock3: That request was already handled, or it expired",
               "%s pressed a button on a request that is no longer open. Nothing "
               "was done. Buttons stay live for %dh."
               % (who, TOKEN_TTL // 3600), "#888888")
        return {"ephemeral_text": "This was already handled, or it has expired."}

    ok, result = actions.run(pending.get("action", ""), pending.get("args", {}))
    label = pending.get("label") or pending.get("action", "")

    # A REFUSAL IS NOT A RESULT. Burning the tokens first is what stops one
    # message being actioned twice, and that part is right. It also means a
    # refused action leaves a message with no buttons and a green tick, where
    # the only record that nothing happened is a line of small print. Put the
    # buttons back and say plainly that it did not happen.
    if not ok:
        give_back(burned)
        print("%s -> %s REFUSED: %s" % (who, label, result), flush=True)
        notify(":warning: %s did NOT happen" % label,
               "`%s`\n\nAsked by %s. The buttons are still there, so try again "
               "once that is dealt with." % (result, who))
        return {"ephemeral_text":
                "**%s did not happen.** %s\n\nThe buttons are still there."
                % (label, result)}

    print("%s -> %s: %s" % (who, label, result), flush=True)
    notify(":white_check_mark: %s" % label, "`%s`\n\nBy %s." % (result, who),
           "#3db665")
    return {"ephemeral_text": "%s: %s" % (label, result),
            "update": {"props": {"attachments": [
                {"color": "#5cb85c", "title": ":white_check_mark: %s" % label,
                 "text": "`%s`\nby %s" % (result, who)}]}}}


def handle(path, name):
    try:
        with open(path) as fh:
            req = json.load(fh)
        if not isinstance(req, dict):
            raise ValueError("not an object")
    except Exception as exc:
        print("queue: dropping unreadable %s: %s" % (name, exc), flush=True)
        try:
            os.unlink(path)
        except OSError:
            pass
        return

    # UNLINK BEFORE ACTING. If this process dies part way through, the request
    # must not be replayed on the next start. A repeated action is survivable;
    # a repeated one nobody asked for is not.
    try:
        os.unlink(path)
    except OSError:
        pass

    try:
        out = decide(req)
    except Exception as exc:
        print("queue: action failed: %s" % exc, flush=True)
        out = {"ephemeral_text": "That failed. See the worker log."}

    rid = name[:-4]
    tmp = os.path.join(QUEUE, rid + ".res.tmp")
    try:
        with open(tmp, "w") as fh:
            json.dump(out, fh)
        os.replace(tmp, os.path.join(QUEUE, rid + ".res"))
    except OSError as exc:
        print("queue: cannot answer %s: %s" % (rid, exc), flush=True)


def sweep():
    cutoff = now() - REPLY_TTL
    for name in os.listdir(QUEUE):
        path = os.path.join(QUEUE, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            pass


def main():
    os.makedirs(QUEUE, exist_ok=True)
    if not ALLOWED:
        print("WARNING: ALLOWED_USER_IDS is empty, so every button will refuse. "
              "That is deliberate. Set it to the ids allowed to act.", flush=True)
    print("worker up, queue at %s, %d operator(s), actions: %s"
          % (QUEUE, len(ALLOWED), ", ".join(sorted(actions.TABLE))), flush=True)
    while True:
        try:
            for name in sorted(os.listdir(QUEUE)):
                # Only .req. A half-written request is still called .tmp, and
                # the rename into .req is what publishes it.
                if name.endswith(".req"):
                    handle(os.path.join(QUEUE, name), name)
            sweep()
        except Exception as exc:
            print("worker loop error: %s" % exc, flush=True)
        # The heartbeat is written HERE, in the loop that has to be alive for a
        # button to do anything. Written anywhere else, a healthcheck would
        # happily call a deaf worker healthy.
        try:
            with open(HEARTBEAT, "w") as fh:
                fh.write(str(now()))
        except OSError:
            pass
        time.sleep(0.3)


if __name__ == "__main__":
    main()
