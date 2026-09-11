#!/usr/bin/env python3
"""THE ACTION TABLE. This is the file you edit.

Everything a button is able to do is in TABLE below, and nothing in a request
can add to it. A request names a key; it never carries a command. That is the
second half of the wall: the edge cannot reach a privilege, and the worker
cannot be talked into doing something that is not written here.

The examples restart a container and run a command, both chosen from fixed
lists, because that is the honest shape of the problem. The privilege being
fenced off is a Docker socket, and a Docker socket is root on the host.
"""
import http.client
import json
import os
import socket
import subprocess

DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
# Long enough for a container with a real shutdown to stop and come back, short
# enough that a wedged action does not hold the queue.
TIMEOUT = int(os.environ.get("ACTION_TIMEOUT", "120"))

# Containers a button may restart. A name that is not here is refused, so the
# blast radius is exactly what you type in ALLOWED_CONTAINERS and nothing wider,
# whatever arrives in a request.
ALLOWED_CONTAINERS = [c.strip() for c in
                      os.environ.get("ALLOWED_CONTAINERS", "").split(",") if c.strip()]

# Commands a button may run, by name. The argv is written here; the request
# chooses between these keys and cannot contribute a word to any of them.
COMMANDS = {
    "disk-free": ["df", "-h", "/"],
}


class UnixHTTPConnection(http.client.HTTPConnection):
    """Docker's API over its unix socket, with no Docker CLI in the image.

    Talking to the API directly keeps this container down to Python and the
    socket. Installing a CLI would mean a package fetch at build or run time in
    the one container that must be hardest to influence.
    """

    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)
        self.sock = sock


def docker(method, path):
    conn = UnixHTTPConnection(DOCKER_SOCKET, TIMEOUT)
    try:
        conn.request(method, path, headers={"Host": "docker"})
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "ignore")
    finally:
        conn.close()


def run_command_argv(argv):
    """Run it, and judge it by its exit code.

    THE EXIT CODE IS THE ANSWER, not the output. Reading the last line and
    calling it the result renders every refusal as a success: the tool says "I
    will not do that while a job is running", and the operator gets a green tick
    with the refusal printed underneath in small type, as though it were a
    receipt. That shipped once. It is the reason this returns a pair.
    """
    try:
        r = subprocess.run(argv, capture_output=True, timeout=TIMEOUT)
        out = (r.stdout or r.stderr).decode("utf-8", "ignore").strip()
        text = out.splitlines()[-1] if out else " ".join(argv)
        return r.returncode == 0, text
    except Exception as exc:
        return False, "failed: %s" % exc


def restart_container(args):
    name = str(args.get("container", ""))
    if name not in ALLOWED_CONTAINERS:
        # Refused here, not at the edge. The edge has no idea what a container
        # is, and that is exactly the point.
        return False, "%s is not in ALLOWED_CONTAINERS" % (name or "no container named")
    # A PLAIN RESTART, never a kill. Ending a container outright is a decision
    # worth typing out on a command line; it is not something one click in a
    # chat window should be able to do to whoever is using it.
    try:
        status, body = docker("POST", "/v1.43/containers/%s/restart?t=10" % name)
    except OSError as exc:
        return False, "cannot reach the Docker socket: %s" % exc
    if status == 204:
        return True, "restarted %s" % name
    try:
        message = json.loads(body).get("message", body)
    except ValueError:
        message = body
    return False, "restart refused (%d): %s" % (status, message.strip()[:200])


def run_command(args):
    name = str(args.get("command", ""))
    argv = COMMANDS.get(name)
    if argv is None:
        return False, "%s is not in COMMANDS" % (name or "no command named")
    return run_command_argv(argv)


def echo(args):
    """Harmless, for a first deploy and for the test suite."""
    return True, "ok: %s" % str(args.get("text", ""))[:200]


TABLE = {
    "restart-container": restart_container,
    "run-command": run_command,
    "echo": echo,
}


def run(name, args):
    """Look up an action and carry it out. Returns (worked, one readable line)."""
    fn = TABLE.get(name)
    if fn is None:
        return False, "no such action: %s" % (name or "(none)")
    if not isinstance(args, dict):
        return False, "malformed arguments"
    return fn(args)
