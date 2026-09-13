#!/bin/bash
# End-to-end tests for the wall between the click and the privilege.
#
# The design claims are all negative ones: the exposed container cannot reach
# the Docker socket, cannot reach the internet, cannot reach the worker, cannot
# forward a field nobody declared, and cannot fill the disk. A negative claim
# that nobody tests is a comment, so each one is asserted here against a running
# stack rather than argued for in the README.
#
# Requires: docker, docker compose, a running stack.
#
#   ./tests/e2e-privilege-wall.sh
#
# Tests are dispatched indirectly via run_test "$name"; shellcheck cannot trace
# that and flags every function as unused (SC2329).
# shellcheck disable=SC2329

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-chatops-wall}"
DOCKER_COMPOSE_FILE="${DOCKER_COMPOSE_FILE:-chatops-privilege-wall-docker-compose.yml}"
OPERATOR="${TEST_OPERATOR_ID:-operator-1}"
VICTIM="wall-test-target"

dc() { docker compose -f "$DOCKER_COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" "$@"; }

EDGE="$(dc ps -aq edge | head -n 1)"
WORKER="$(dc ps -aq worker | head -n 1)"
[[ -n "$EDGE" ]] || { echo "error: the edge container was not found" >&2; exit 1; }
[[ -n "$WORKER" ]] || { echo "error: the worker container was not found" >&2; exit 1; }

PASSED=0
FAILED=0
FAILURES=()
run_test() {
  local name="$1"
  echo
  echo "=== $name ==="
  if "$name"; then
    echo "  PASS: $name"; PASSED=$((PASSED + 1))
  else
    echo "  FAIL: $name" >&2; FAILED=$((FAILED + 1)); FAILURES+=("$name")
  fi
}

# A click, sent from inside the edge to the edge. Prints the status code on the
# first line and the body on the second.
click() {
  dc exec -T edge python3 -c '
import sys, urllib.request, urllib.error
data = sys.stdin.buffer.read()
req = urllib.request.Request("http://127.0.0.1:8790/act", data=data,
                             headers={"Content-Type": "application/json"})
try:
    r = urllib.request.urlopen(req, timeout=40)
    print(r.status); print(r.read().decode().replace("\n", " "))
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode().replace("\n", " "))
' <<<"$1"
}

as_operator() {
  printf '{"context":{"token":"%s"},"user_id":"%s","user_name":"tester"%s}' \
    "$1" "$OPERATOR" "${2:-}"
}

mint() {
  dc exec -T worker python3 /app/mint.py --json "$@" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])'
}

queue_files() { dc exec -T worker sh -c 'ls -1 /queue 2>/dev/null' | tr -d '\r'; }

test_the_edge_holds_no_docker_socket() {
  local mounts
  mounts="$(docker inspect "$EDGE" --format '{{range .Mounts}}{{.Destination}} {{end}}')"
  grep -q 'docker.sock' <<<"$mounts" && { echo "  it has one: $mounts" >&2; return 1; }
  dc exec -T edge sh -c 'test -e /var/run/docker.sock' 2>/dev/null \
    && { echo "  a docker socket exists inside the edge" >&2; return 1; }
  echo "  mounts are: $mounts"
}

test_the_edge_has_no_route_out() {
  # A raw address, not a name: on an internal network the name would fail to
  # resolve and the test would pass without ever proving a packet cannot leave.
  local out
  out="$(dc exec -T edge python3 -c '
import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=6)
    print("CONNECTED")
except Exception as e:
    print("blocked: %s" % type(e).__name__)
' | tr -d '\r')"
  grep -q CONNECTED <<<"$out" && { echo "  the edge reached the internet" >&2; return 1; }
  echo "  $out"
}

test_the_edge_cannot_reach_the_worker() {
  local out
  out="$(dc exec -T edge python3 -c '
import socket
try:
    print("RESOLVED %s" % socket.gethostbyname("worker"))
except Exception as e:
    print("no route to the worker: %s" % type(e).__name__)
' | tr -d '\r')"
  grep -q RESOLVED <<<"$out" && { echo "  the edge can see the worker: $out" >&2; return 1; }
  echo "  $out"
}

test_the_edge_filesystem_is_read_only_apart_from_the_queue() {
  dc exec -T edge sh -c 'touch /app/smuggled' 2>/dev/null \
    && { echo "  the edge wrote to /app" >&2; return 1; }
  dc exec -T edge sh -c 'touch /queue/.probe && rm /queue/.probe' \
    || { echo "  the edge cannot write to its own queue" >&2; return 1; }
  echo "  /app is read-only, /queue is writable, and that is the whole of it"
}

test_only_declared_fields_cross_the_wall() {
  # The worker is stopped so the request stays in the queue long enough to be
  # read. Racing the worker for it would make this test flake, not fail.
  dc stop worker > /dev/null
  click "$(as_operator bogus-token ',"command":"rm -rf /","extra":{"a":1}')" > /dev/null || true
  local body
  # Read through the edge, which is up and holds the same volume. A throwaway
  # container would work too and adds a second thing that can fail.
  body="$(dc exec -T edge sh -c 'cat /queue/*.req 2>/dev/null' | tr -d '\r')"
  dc start worker > /dev/null
  echo "  the worker was handed: ${body:-<nothing was queued>}"
  [[ -n "$body" ]] || { echo "  no request reached the queue at all" >&2; return 1; }
  grep -q '"command"' <<<"$body" && { echo "  an undeclared field crossed" >&2; return 1; }
  grep -q '"extra"' <<<"$body" && { echo "  an undeclared field crossed" >&2; return 1; }
  grep -q '"token"' <<<"$body" || { echo "  the declared field did not cross" >&2; return 1; }
  # Give the worker its moment to clear what is left.
  sleep 3
}

test_an_oversized_body_is_refused() {
  local big code
  big="$(python3 -c 'print("{\"pad\":\"" + "x"*20000 + "\"}")')"
  code="$(click "$big" | head -1)"
  [[ "$code" == "413" ]] || { echo "  expected 413, got $code" >&2; return 1; }
  echo "  20000 bytes refused with 413, and nothing was written"
}

test_a_body_that_is_not_an_object_is_refused() {
  local code
  code="$(click '[1,2,3]' | head -1)"
  [[ "$code" == "400" ]] || { echo "  expected 400, got $code" >&2; return 1; }
  code="$(click 'not json at all' | head -1)"
  [[ "$code" == "400" ]] || { echo "  expected 400 for junk, got $code" >&2; return 1; }
  echo "  a list and a junk string are both refused with 400"
}

test_a_full_queue_refuses_instead_of_filling_the_disk() {
  dc stop worker > /dev/null
  local code="" i
  # MAX_QUEUE is 3 in the test .env, so the fourth click has nowhere to go.
  for i in 1 2 3 4 5; do
    code="$(click "$(as_operator "fill-$i")" | head -1)"
    [[ "$code" == "503" ]] && break
  done
  dc exec -T worker sh -c 'true' 2>/dev/null || true
  dc start worker > /dev/null
  sleep 4
  [[ "$code" == "503" ]] || { echo "  the queue never refused, last code $code" >&2; return 1; }
  echo "  the edge answered 503 once the queue was full, rather than writing anyway"
}

test_a_half_written_request_is_never_read() {
  dc exec -T worker sh -c 'printf "{\"token\":\"half\"" > /queue/halfwritten.tmp'
  sleep 2
  queue_files | grep -q 'halfwritten.tmp' \
    || { echo "  the worker consumed a .tmp file" >&2; return 1; }
  dc exec -T worker sh -c 'rm -f /queue/halfwritten.tmp'
  echo "  a .tmp file sat untouched; only the rename into .req publishes a request"
}

test_an_empty_operator_list_refuses_everybody() {
  # Called directly rather than through the queue, so the answer is about the
  # check and not about whatever else is running.
  local out
  out="$(dc run --rm --no-deps -e PYTHONPATH=/app -e ALLOWED_USER_IDS= --entrypoint python3 worker -c '
import json, worker
print(json.dumps(worker.decide({"token": "x", "user_id": "anybody"})))' 2>/dev/null | tr -d '\r')"
  grep -q 'No operators are configured' <<<"$out" \
    || { echo "  an empty list did not refuse: $out" >&2; return 1; }
  echo "  empty means nobody, not 'skip the check'"
}

test_a_refusal_names_the_id() {
  local out
  out="$(dc run --rm --no-deps -e PYTHONPATH=/app -e ALLOWED_USER_IDS=someone-else --entrypoint python3 worker -c '
import json, worker
print(json.dumps(worker.decide({"token": "x", "user_id": "the-wrong-id"})))' 2>/dev/null | tr -d '\r')"
  grep -q 'the-wrong-id' <<<"$out" \
    || { echo "  the refusal does not carry the id: $out" >&2; return 1; }
  echo "  the refusal carries the one value needed to fix it"
}

test_an_unknown_token_is_refused() {
  local out
  out="$(click "$(as_operator "never-issued")" | tail -1)"
  grep -qi 'already handled\|expired' <<<"$out" \
    || { echo "  a token nobody issued was accepted: $out" >&2; return 1; }
  echo "  refused: $out"
}

test_a_token_is_spent_exactly_once() {
  local token first second
  token="$(mint --action echo --arg text=once --label "Echo once")"
  first="$(click "$(as_operator "$token")" | tail -1)"
  second="$(click "$(as_operator "$token")" | tail -1)"
  grep -q 'ok: once' <<<"$first" || { echo "  the first click failed: $first" >&2; return 1; }
  grep -qi 'already handled\|expired' <<<"$second" \
    || { echo "  the second click was honoured too: $second" >&2; return 1; }
  echo "  first: $first"
  echo "  second: $second"
}

test_a_refused_action_gives_the_button_back() {
  local token first second
  token="$(mint --action restart-container --arg container=not-on-the-list --label "Restart")"
  first="$(click "$(as_operator "$token")" | tail -1)"
  grep -q 'did not happen' <<<"$first" \
    || { echo "  a container off the list was not refused: $first" >&2; return 1; }
  # The token must still be there. A refusal has answered nothing, and a button
  # that vanishes on refusal leaves a message with a green tick and no record.
  second="$(click "$(as_operator "$token")" | tail -1)"
  grep -q 'did not happen' <<<"$second" \
    || { echo "  the token was spent by a refusal: $second" >&2; return 1; }
  echo "  refused twice with the button intact: $first"
}

test_a_click_really_restarts_the_container() {
  local before after token out
  before="$(docker inspect "$VICTIM" --format '{{.State.StartedAt}}')"
  token="$(mint --action restart-container --arg container="$VICTIM" --label "Restart target")"
  out="$(click "$(as_operator "$token")" | tail -1)"
  after="$(docker inspect "$VICTIM" --format '{{.State.StartedAt}}')"
  echo "  $out"
  [[ "$before" != "$after" ]] || { echo "  the container did not restart" >&2; return 1; }
  echo "  started at $before, then at $after"
}

test_the_worker_is_judged_by_its_queue_loop() {
  # SIGSTOP to the process, not a stop of the container. The container stays up
  # and every check of "is it running" keeps saying yes; only a check that reads
  # what the queue loop writes can tell that nothing is being answered.
  #
  # Sent from the host on purpose. A signal sent from inside the container is
  # never delivered to PID 1 unless that process installed a handler, so
  # `kill -STOP 1` in an exec session is silently nothing at all.
  local status elapsed=0
  docker kill --signal=STOP "$WORKER" > /dev/null
  while [[ $elapsed -lt 120 ]]; do
    status="$(docker inspect "$WORKER" --format '{{.State.Health.Status}}')"
    [[ "$status" == "unhealthy" ]] && break
    sleep 5; elapsed=$((elapsed + 5))
  done
  local running
  running="$(docker inspect "$WORKER" --format '{{.State.Running}}')"
  docker kill --signal=CONT "$WORKER" > /dev/null
  [[ "$status" == "unhealthy" ]] || {
    echo "  a stopped queue loop still reported $status after ${elapsed}s" >&2; return 1; }
  [[ "$running" == "true" ]] || {
    echo "  the container was not running, so this proved nothing" >&2; return 1; }
  echo "  the container was still running and the healthcheck said unhealthy"
  elapsed=0
  while [[ $elapsed -lt 120 ]]; do
    status="$(docker inspect "$WORKER" --format '{{.State.Health.Status}}')"
    [[ "$status" == "healthy" ]] && break
    sleep 5; elapsed=$((elapsed + 5))
  done
  [[ "$status" == "healthy" ]] || { echo "  it did not recover: $status" >&2; return 1; }
  echo "  and healthy again once the loop resumed"
}

# The two below are one test in two halves, and neither half is worth anything
# alone. Proving the canary is quiet proves nothing if the notifier is broken:
# a check that only ever demonstrates silence cannot tell a working exemption
# from a notifier that stopped working altogether. So one asserts the canary
# does not reach the channel and the other asserts an ordinary refusal does.
test_a_canary_refusal_is_logged_and_not_announced() {
  local out
  out="$(dc run --rm --no-deps -e PYTHONPATH=/app \
        -e ALLOWED_USER_IDS=someone-else -e NOTIFY_URL= --entrypoint python3 worker -c '
import json, worker
print(json.dumps(worker.decide({"token": "x", "user_id": "canary0000000000000000"})))' 2>&1 | tr -d '\r')"
  grep -q "canary probe: operator-list refusal not announced" <<<"$out" \
    || { echo "  the canary was not recognised: $out" >&2; return 1; }
  grep -q "not allowed" <<<"$out" \
    || { echo "  the canary was not refused, which is the point of the probe" >&2; return 1; }
  echo "  refused, logged as a probe, and not sent to the channel"
}

test_an_ordinary_refusal_is_still_announced() {
  local out
  out="$(dc run --rm --no-deps -e PYTHONPATH=/app \
        -e ALLOWED_USER_IDS=someone-else -e NOTIFY_URL=http://127.0.0.1:9/hook \
        --entrypoint python3 worker -c '
import json, worker
print(json.dumps(worker.decide({"token": "x", "user_id": "a-real-person"})))' 2>&1 | tr -d '\r')"
  grep -q "canary probe" <<<"$out" \
    && { echo "  an ordinary id was treated as a canary" >&2; return 1; }
  # Port 9 discards, so the attempt fails and says so. That failure is the
  # proof: the notifier was reached for this id and skipped for the canary.
  grep -q "notify failed" <<<"$out" \
    || { echo "  no announcement was attempted for an ordinary refusal: $out" >&2; return 1; }
  echo "  announced (the attempt reached the notifier and was reported)"
}

run_test test_the_edge_holds_no_docker_socket
run_test test_the_edge_has_no_route_out
run_test test_the_edge_cannot_reach_the_worker
run_test test_the_edge_filesystem_is_read_only_apart_from_the_queue
run_test test_only_declared_fields_cross_the_wall
run_test test_an_oversized_body_is_refused
run_test test_a_body_that_is_not_an_object_is_refused
run_test test_a_full_queue_refuses_instead_of_filling_the_disk
run_test test_a_half_written_request_is_never_read
run_test test_an_empty_operator_list_refuses_everybody
run_test test_a_refusal_names_the_id
run_test test_an_unknown_token_is_refused
run_test test_a_token_is_spent_exactly_once
run_test test_a_refused_action_gives_the_button_back
run_test test_a_click_really_restarts_the_container
run_test test_the_worker_is_judged_by_its_queue_loop
run_test test_a_canary_refusal_is_logged_and_not_announced
run_test test_an_ordinary_refusal_is_still_announced

echo
echo "=== $PASSED passed, $FAILED failed ==="
if [[ $FAILED -gt 0 ]]; then
  printf '  %s\n' "${FAILURES[@]}" >&2
  exit 1
fi
