#!/usr/bin/env python3
"""The half of the system that faces the internet, and that can do nothing else.

WHY THIS IS A SEPARATE PROCESS.

The obvious way to build a chat button that restarts a service is one process:
it listens for the click and it holds a Docker socket. That process is then the
most exposed component you run and the most privileged one at the same time,
and only the correctness of a small JSON handler stands between those two facts.
A Docker socket is not "a way to run containers". It is root on the host,
because anything holding it can start a container that mounts the root
filesystem.

So the two ends are two containers here:

  edge (this)   listens, and can do nothing. No Docker socket, no host mounts,
                no credentials, no route to the worker. Its entire vocabulary is
                "write a file into the queue directory".
  worker        reads the queue, decides, and acts. It holds whatever privilege
                the action needs and it has NO inbound network path at all:
                nothing is published and it listens on no port.

IT DECIDES NOTHING. Not whether the token is valid, not whether the caller is
allowed, not what the action means. It copies a declared list of fields out of
the request and forwards them. Every judgement happens behind the wall. That is
what makes it safe to expose: a component with no authority cannot be tricked
into exercising any.

A DIRECTORY, NOT A SOCKET, is the channel on purpose. The listener needs write
access to one directory and nothing else, so there is nothing here that could
carry more authority than a filename.

THE ANSWER COMES BACK SYNCHRONOUSLY, because chat platforms apply a message
update from the HTTP response body. Fire-and-forget would leave every card
unanswered on screen. So this waits for the worker's reply file, briefly, and
passes it through untouched.
"""
import http.server
import json
import os
import secrets
import socketserver
import sys
import time

QUEUE = os.environ.get("QUEUE_DIR", "/queue")
PORT = int(os.environ.get("EDGE_PORT", "8790"))
PATH = os.environ.get("EDGE_PATH", "/act")
# Long enough for a real action to finish and be read back, short enough that a
# wedged worker does not hold a browser open.
WAIT = float(os.environ.get("RESULT_WAIT", "25"))
# A request here is a handful of short strings. Anything larger is not one.
MAX_BODY = int(os.environ.get("MAX_BODY", "8192"))
# A cap on how much unanswered work may pile up. Without it, an open endpoint
# plus a stopped worker equals a full disk, which is the least interesting way
# for a host to fall over.
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "100"))
# How much of any one value is forwarded. The privileged side never sees a
# string long enough to be interesting.
FIELD_MAX = int(os.environ.get("FIELD_MAX", "128"))

# THE FIELDS THIS IS ALLOWED TO FORWARD, and nothing else reaches the worker.
# "name=dotted.path" per entry, read from the incoming JSON. Keep the list
# short: every field here is a field the privileged side has to be careful
# about, and a field that is not listed cannot be smuggled through by adding it
# to the request.
FIELDS = [f.strip() for f in os.environ.get(
    "FORWARD_FIELDS",
    "token=context.token,user_id=user_id,user_name=user_name").split(",")
    if f.strip()]


def dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict):
            return ""
        obj = obj.get(part)
    return "" if obj is None else obj


def queued():
    try:
        return sum(1 for f in os.listdir(QUEUE) if f.endswith(".req"))
    except OSError:
        return 0


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "chatops-edge"

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Health only, and it says nothing about state: this endpoint is public.
        if self.path == "/health":
            return self.reply(200, {"ok": True, "queued": queued()})
        return self.reply(404, {})

    def do_POST(self):
        if self.path != PATH:
            return self.reply(404, {})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self.reply(400, {"ephemeral_text": "bad request"})
        if n > MAX_BODY:
            return self.reply(413, {"ephemeral_text": "too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except Exception:
            return self.reply(400, {"ephemeral_text": "bad request"})

        if queued() >= MAX_QUEUE:
            return self.reply(503, {"ephemeral_text":
                                    "Not answering right now. Try again shortly."})

        req = {}
        for spec in FIELDS:
            name, _, path = spec.partition("=")
            req[name] = str(dig(body, path or name))[:FIELD_MAX]

        rid = secrets.token_hex(8)
        tmp = os.path.join(QUEUE, rid + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(req, fh)
        # Renamed last. The worker must never see a half-written request, and a
        # rename within one directory is the only step here that is atomic.
        os.replace(tmp, os.path.join(QUEUE, rid + ".req"))

        res = os.path.join(QUEUE, rid + ".res")
        deadline = time.time() + WAIT
        while time.time() < deadline:
            try:
                with open(res) as fh:
                    out = json.load(fh)
                os.unlink(res)
                return self.reply(200, out)
            except FileNotFoundError:
                time.sleep(0.2)
            except Exception:
                break
        # The work is queued and will still happen. Only the message update is
        # lost, and saying so beats a button that appears to have done nothing.
        return self.reply(200, {"ephemeral_text":
                                "Sent. It is taking longer than usual."})

    def log_message(self, *args):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    try:
        os.makedirs(QUEUE, exist_ok=True)
        probe = os.path.join(QUEUE, ".edge-write-probe")
        with open(probe, "w") as fh:
            fh.write("")
        os.unlink(probe)
    except OSError as exc:
        # Refuse to start rather than accept clicks it cannot queue. An endpoint
        # that answers 200 and drops the work is worse than one that is down.
        sys.exit("edge: cannot write to the queue at %s: %s" % (QUEUE, exc))
    print("edge up on %d%s, queue at %s, forwarding %s, holding no privilege"
          % (PORT, PATH, QUEUE, ",".join(f.split("=")[0] for f in FIELDS)),
          flush=True)
    Server(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
