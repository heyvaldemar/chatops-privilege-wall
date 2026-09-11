# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_(no unreleased changes yet)_

## [1.1.0] - 2026-09-11

### Added

- **`update.sh`, and a CI run that upgrades rather than starts fresh.** A
  deployed host had no way to move between release tags, and CI proved only
  that the current release boots on empty volumes. Neither says anything about
  what a deployed host actually does, which is `git pull && docker compose
  up -d` over a token store and a queue the previous release wrote.

  The script refuses to cross a major version unattended, refuses to run over
  local changes, and names any variable that became required since your version
  before anything has moved. It waits for the worker to report healthy before
  calling the update finished: the worker is judged by its queue loop, and one
  that comes back unable to read its queue leaves every button silently doing
  nothing while `up -d` returns 0 over it.

  CI now starts the previous release on this project's volumes first, waits for
  it to be healthy, and stops it keeping the volumes, so the ordinary `up -d`
  that follows is an upgrade and every check after it judges an upgraded stack.

## [1.0.0] - 2026-09-11

### Added

- **A wall between a chat button and the privilege it triggers.** The usual
  build is one container that listens for the click and holds a Docker socket,
  which makes the most exposed component you run the most privileged one as
  well. A Docker socket is root on the host: anything holding it can start a
  container that mounts `/`, and `:ro` on the mount changes nothing because the
  API is the same API either way.

  Two containers here. The `edge` listens and can do nothing else: no socket, no
  host mounts, no credentials, a read-only filesystem, every capability dropped,
  and a network declared `internal: true` so it has no route out. Its entire
  vocabulary is "write a file into the queue directory". The `worker` reads that
  directory, judges, and acts. It has no inbound path at all: nothing published,
  no port, and it is not on the network the edge is on.

  The two share one volume and nothing else. A directory is the channel on
  purpose, because there is nothing in a directory that can carry more authority
  than a filename.

- **Only declared fields cross.** `FORWARD_FIELDS` names them, each is
  stringified and truncated, and everything else in a request stops at the edge.
  The privileged side never sees an object it did not ask for.

- **An empty operator list means nobody.** Written the natural way, `if ALLOWED
  and user not in ALLOWED`, an empty list stops meaning "nobody" and starts
  meaning "skip the check". That shipped in the system this template comes from,
  the list was empty the whole time, and nothing had gone wrong only because
  nobody had tried.

- **A refusal names the id and is announced where it can be seen.** An action
  was once refused because the id recorded for an operator was not that
  operator's id, and the log named the human rather than the one value that
  could be compared against the list. Separately, three operators clicked
  against a stale allowlist, all three were refused, and all three saw nothing:
  the reply in an HTTP response is one copy, visible to one person, in a place
  the worker cannot read back.

- **A refused action gives the buttons back.** Tokens are spent on use so one
  message cannot be actioned twice, which also meant a declined action left a
  message with no buttons and a green tick. A refusal restores them and says
  plainly that nothing happened.

- **Actions are judged by exit code, not by output.** Reading the last line and
  calling it the result renders every refusal as a success.

- **The worker is judged by its queue loop.** The heartbeat is written in the
  loop that has to be alive for a button to mean anything, so a healthcheck
  cannot call a deaf worker healthy. `HEARTBEAT_MAX_AGE` defaults above
  `ACTION_TIMEOUT`, because the loop writes the heartbeat between requests and a
  shorter window would report a healthy worker as dead every time somebody
  restarts something sizeable.

- **Caps on what an open endpoint can cost you.** `MAX_BODY`, `FIELD_MAX`, and
  `MAX_QUEUE`, which answers 503 rather than writing: an open endpoint plus a
  stopped worker is otherwise a full disk.

- **A request is published by a rename.** It is written as `.tmp` and renamed to
  `.req`, so the worker never reads a half-written file, and it is unlinked
  before the action runs, so a worker that dies part way through does not replay
  it on the next start.

- `tests/e2e-privilege-wall.sh`, sixteen scenarios against a running stack, run
  by CI. The design's claims are negative ones and a negative claim nobody tests
  is a comment.


[Unreleased]: https://github.com/heyvaldemar/chatops-privilege-wall/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/heyvaldemar/chatops-privilege-wall/releases/tag/v1.1.0
[1.0.0]: https://github.com/heyvaldemar/chatops-privilege-wall/releases/tag/v1.0.0
