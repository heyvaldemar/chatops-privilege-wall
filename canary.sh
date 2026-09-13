#!/bin/bash

# Prove the endpoint is still refusing correctly, by being refused by it.
#
#     chmod +x canary.sh
#     ./canary.sh                        # probe, print, exit 0 if both gates hold
#     ./canary.sh --url https://host/act # a different endpoint
#
# Run it from a timer. A privileged endpoint nobody probes is one you find out
# about on the day it matters, and this one cannot be probed by doing anything:
# every action it can perform is an action you did not ask for. So it is probed
# by being turned away, twice, at the two gates that must never stop working.
#
#   Probe one  an id that is NOT on the operator list. Refused by the list.
#              If this ever succeeds, the list has stopped being consulted.
#   Probe two  an id that IS on the list, with a token nobody issued. Refused
#              by token validation. This one has to pass the operator list to
#              reach the gate it tests, which is why CANARY_OPERATOR_ID must be
#              in ALLOWED_USER_IDS.
#
# BOTH REFUSALS ARE THE CHECK PASSING, so the worker logs them and does not
# announce them. Two messages a day saying nothing happened is how a channel
# stops being read, and a channel nobody reads is where the refusal that
# mattered goes to die.
#
# THE OPERATOR ID MUST BE UNMISTAKABLE. In the system this comes from it looked
# exactly like a person's, sat in the list under a comment naming colleagues,
# was read as stale during a tidy-up, and was deleted. The health check it
# belonged to broke that night. Write it so that nobody can read it and think
# it belongs to someone.
set -uo pipefail

URL="${ACTION_URL:-}"
# Both ids must begin with CANARY_ID_PREFIX (default "canary") or the worker
# will announce these refusals to your channel, twice a day, forever.
OUTSIDER="${CANARY_OUTSIDER_ID:-canary0000000000000000}"
OPERATOR="${CANARY_OPERATOR_ID:-canary1111111111111111}"
TIMEOUT="${CANARY_TIMEOUT:-20}"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    -h|--help) sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    # A junk argument is rejected rather than ignored. Ignoring it would run the
    # probe the caller did not ask for and report success for it.
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$URL" ]; then
  echo "set ACTION_URL, or pass --url https://your-host/act" >&2
  exit 2
fi

probe() {
  local uid="$1" token="$2"
  curl -sS --max-time "$TIMEOUT" -X POST "$URL" \
    -H "Content-Type: application/json" \
    --data "{\"context\":{\"token\":\"$token\"},\"user_id\":\"$uid\",\"user_name\":\"canary\"}" 2>&1
}

problems=""

echo "--> probe one: an id that is not on the operator list"
one="$(probe "$OUTSIDER" "canary-no-such-token")"
echo "    $one"
case "$one" in
  *"not allowed"*) echo "    the operator list refused it, as it must" ;;
  *) problems="$problems
  the operator list did not refuse an unlisted id" ;;
esac

echo "--> probe two: a listed id, with a token nobody issued"
two="$(probe "$OPERATOR" "canary-no-such-token")"
echo "    $two"
case "$two" in
  # Reaching this message proves the request passed the operator list AND was
  # then stopped by token validation. If it says "not allowed" instead, the
  # canary's own id has fallen off the list and this probe is testing nothing.
  *"already handled"*|*"expired"*) echo "    token validation refused it, as it must" ;;
  *"not allowed"*) problems="$problems
  $OPERATOR is not in ALLOWED_USER_IDS, so this probe stopped at the operator
  list and never reached token validation. Put it back." ;;
  *) problems="$problems
  token validation did not refuse a token nobody issued" ;;
esac

if [ -n "$problems" ]; then
  echo
  echo "CANARY FAILED:$problems" >&2
  exit 1
fi
echo
echo "canary ok: both gates refused, the endpoint is answering"
