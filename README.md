# A chat button that restarts a service, without giving the internet root

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/14896/badge)](https://www.bestpractices.dev/projects/14896)

The usual way to build this is one container. It listens for the click and it
holds a Docker socket, so the most exposed component you run is also the most
privileged one, and the only thing between those two facts is a small JSON
handler you wrote on a Tuesday.

A Docker socket is not a way to run containers. It is root on the host: anything
holding it can start a container that mounts `/`. Mounting it read-only changes
nothing, because the API is the same API in either direction.

This template is that system with a wall down the middle. The half strangers can
reach can do exactly one thing: write a file into a directory. The half that can
act has no way in at all.

## Getting started

```bash
# 1. Clone
git clone https://github.com/heyvaldemar/chatops-privilege-wall.git
cd chatops-privilege-wall

# 2. Create the network Traefik uses
docker network create traefik-network

# 3. Copy the environment template and fill in required values
cp .env.example .env
# ^ Required: WALL_HOSTNAME, TRAEFIK_HOSTNAME, TRAEFIK_ACME_EMAIL,
#   TRAEFIK_BASIC_AUTH, and ALLOWED_USER_IDS. The last one starts empty,
#   and while it is empty every button refuses.

# 4. Deploy
docker compose -f chatops-privilege-wall-docker-compose.yml -p chatops-wall up -d

# 5. Issue a button and post what it prints to your chat platform
docker compose -p chatops-wall exec worker python3 /app/mint.py \
  --action echo --arg text=hello --label "Say hello"
```

### What success looks like

```bash
curl -s https://your-hostname/health
# Expected: {"ok": true, "queued": 0}
docker compose -p chatops-wall ps
# Expected: edge, worker and traefik all healthy
```

## The shape, and why it is this shape

```
  the internet  ──▶  traefik  ──▶  edge  ──▶  a directory  ──▶  worker  ──▶  docker
                                    │                             │
                              no socket                     no inbound path
                           no route out                     no open port
                        no credentials                   not on the edge's network
```

The two containers share one volume and nothing else. No network path between
them, no socket, no shared secret. The edge writes a request file; the worker
reads it. A directory is the channel on purpose: the listener needs write access
to one directory and nothing else, so there is nothing in the channel that could
carry more authority than a filename.

**The edge decides nothing.** Not whether the token is real, not whether the
clicker is allowed, not what the action means. It copies a declared list of
fields and forwards them. That is what makes it safe to expose, and it is the
one property worth protecting when you extend this: a component with no
authority cannot be tricked into exercising any.

**Only declared fields cross.** `FORWARD_FIELDS` names them. A request can carry
anything it likes; the privileged side never sees an object it did not ask for,
and never a string longer than `FIELD_MAX`.

**The edge has no route out.** Its network is declared `internal: true` in the
compose file rather than created by hand, because this is a property of the
design and not a flag somebody has to remember to pass to `docker network
create`. Traefik sits on both networks and carries requests in.

**The worker has no way in.** Nothing is published, it listens on no port, and
it is not on the network the edge is on. It reaches out to your chat webhook and
nothing reaches it.

**A request names an action, never a command.** Everything a button can do is in
`worker/actions.py`, and nothing in a request can add to it.

## Five things that cost an evening each

**An allowlist that is empty must mean nobody.** Written the natural way,
`if ALLOWED and user not in ALLOWED`, an empty list stops meaning "nobody" and
starts meaning "skip the check". The system this template comes from shipped
that way, the list was empty the whole time, and nothing had gone wrong only
because nobody had tried. A setting whose blank value switches off the thing it
configures is a trap for whoever tidies the file next.

**A refusal has to name the id, not just the person.** A real action was once
refused because the id recorded for an operator was not that operator's id: the
account had been rebuilt and the list kept the old one. The log said who was
refused, which is the one thing you already know. It now says which id was
presented, because that is the value you need to fix it. An id is not a secret;
every id it is checked against sits in the configuration in the clear.

**A refusal has to be announced somewhere it can be seen.** The reply carried in
the HTTP response is one copy, visible to one person, in a place this process
cannot read back. Three operators once clicked, all three were refused because
the whole allowlist was stale, and all three saw nothing at all. Silence is what
let a dead list survive for as long as the buttons had existed.

**A refused action is not a result.** Buttons are spent when they are used, so
one message cannot be actioned twice. That is right, and it also meant an action
the host declined left a message with no buttons and a green tick, with the
refusal underneath in small type like a receipt. A refusal puts the buttons back
and says plainly that nothing happened.

**The exit code is the answer, not the output.** Reading the last line of output
and calling it the result turns every refusal into a success. The tool says "I
will not do that while a job is running" and the operator gets a tick.

## Checking that it still refuses

A privileged endpoint nobody probes is one you find out about on the day it matters, and this one cannot be probed by doing anything: every action it can perform is an action you did not ask for. So it is probed by being turned away, at the two gates that must never stop working.

```bash
ACTION_URL=https://your-host/act ./canary.sh
```

The first probe uses an id that is not on the operator list and must be refused by the list. The second uses an id that **is** on the list with a token nobody issued, and must be refused by token validation. The second one has to pass the operator list to reach the gate it tests, so `CANARY_OPERATOR_ID` belongs in `ALLOWED_USER_IDS` next to the people. When it is missing, the canary says exactly that rather than reporting a vague failure.

Both refusals are the check passing, so the worker logs them and does not announce them. Two messages a day saying nothing happened is how a channel stops being read, and a channel nobody reads is where the refusal that mattered goes to die. The skip prints its own log line, so the path taken is visible rather than assumed.

**Write the canary's id so that nobody could mistake it for a person.** This is not style. In the system this pattern comes from, the canary's entry looked exactly like a colleague's id and sat under a comment naming three people. During a tidy-up the whole list was read as stale and the entry was deleted with the dead ones. The health check broke that night, and the first thing anybody saw was two refusal cards at one minute past midnight. `canary1111111111111111` could not have been deleted by that mistake.

## Extending it

Add an entry to `TABLE` in `worker/actions.py`. Each action takes a dict of
arguments fixed at mint time and returns `(worked, one readable line)`. Nothing
else has to change, and nothing in `edge.py` should: every line you add there is
a line running in the container strangers can reach.

The arguments come from `mint.py`, not from the click. A person with a shell
decides what a button will do; the click only chooses whether to do it.

## Production checklist

- [ ] **Set `ALLOWED_USER_IDS`.** Until you do, every button refuses, which is
      the correct empty state and a useless running one.
- [ ] **Check it again after anybody's account is rebuilt.** A stale id is
      indistinguishable from a hostile one, and that is the point.
- [ ] **Keep `ALLOWED_CONTAINERS` to what you meant.** It is the blast radius of
      a click.
- [ ] **Set `NOTIFY_URL`,** so a refusal is not carried only in a reply nobody
      can read back.
- [ ] **Verify your chat platform's request signature** if it offers one, in
      `worker/actions.py` or alongside the token check. The edge deliberately
      does not: verification needs a shared secret, and the whole design is that
      the exposed half holds none.
- [ ] **Put the canary's operator id in `ALLOWED_USER_IDS`,** and write it so
      it cannot be mistaken for a person's.
- [ ] **Run `canary.sh` from a timer.** A gate nobody tests is a gate you find
      out about on the day it stops working.
- [ ] **Leave the socket where it is.** If anything is ever added to the edge's
      volume list, the arrangement is gone.

## Updating

`./update.sh` moves this checkout to the latest release tag, a combination this repository's CI has booted, upgraded from the previous release on the same volumes, and tested. It refuses to cross a major version unattended, refuses to run over local changes, and names any variable that became required since your version before anything has moved. `./update.sh --dry-run` says what would happen.

It waits for the worker to report healthy before calling the update finished, because the worker is judged by its queue loop: one that comes back unable to read its queue leaves every button silently doing nothing, and `up -d` returns 0 over that.

## Testing

`tests/e2e-privilege-wall.sh` asserts eighteen things against a running stack.
The design's claims are all negative ones, and a negative claim nobody tests is
a comment: that the edge holds no Docker socket, that it cannot reach the
internet, that it cannot reach the worker, that its filesystem is read-only
apart from the queue, that an undeclared field does not cross, that an oversized
body and a non-object body are refused, that a full queue answers 503 instead of
writing anyway, and that a half-written request is never read.

Two of them are one test in halves, and neither half is worth anything alone: the canary's refusal is not announced, and an ordinary refusal still is. Proving only that the canary is quiet cannot tell a working exemption from a notifier that has stopped working altogether.

The rest are the five evenings above, plus the two that matter most in
operation: a click really does restart the container, and a worker whose queue
loop has stopped reports unhealthy while its container is still running and
still looks fine to anything that checks for a process.

```bash
./tests/e2e-privilege-wall.sh
```

Run it against a staging stack. It stops the worker, fills the queue, restarts a
container and suspends a process.

The [Deployment Verification](https://github.com/heyvaldemar/chatops-privilege-wall/actions/workflows/deployment-verification.yml?query=branch%3Amain)
workflow runs it on every push, pull request, and daily, alongside shell and
workflow linting, Trivy scans of both pinned images, and a check that each pin
still resolves to the digest recorded here.

## Supply chain trust

Two upstream images, both pinned by `tag@sha256:<digest>` as interpolation
defaults in the compose file's `x-images` block: `traefik` from Docker Hub, and
`python:3.13-alpine` for the edge and the worker, which run the Python in this
repository and nothing else. No image is built here and no dependency is
installed at run time; the worker talks to Docker over its API rather than
carrying a CLI, so the container that must be hardest to influence contains
Python and a socket.

### Verify what you deploy

Every release from v1.2.3 on carries three files made on GitHub's runner with a short-lived identity and no stored key: `chatops-privilege-wall-<tag>.tar.gz`, a `git archive` of exactly the tree the tag points at; `chatops-privilege-wall-<tag>.tar.gz.sigstore.json`, a keyless [Sigstore](https://www.sigstore.dev/) signature over it; and `chatops-privilege-wall-<tag>.intoto.jsonl`, [SLSA](https://slsa.dev/) build provenance from the SLSA generator. To check them with nothing from this repository trusted:

```bash
cosign verify-blob chatops-privilege-wall-<tag>.tar.gz \
  --bundle chatops-privilege-wall-<tag>.tar.gz.sigstore.json \
  --certificate-identity-regexp '^https://github.com/heyvaldemar/chatops-privilege-wall/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

slsa-verifier verify-artifact chatops-privilege-wall-<tag>.tar.gz \
  --provenance-path chatops-privilege-wall-<tag>.intoto.jsonl \
  --source-uri github.com/heyvaldemar/chatops-privilege-wall
```

Add `--source-tag <tag>` for a release published after 24 September 2026, which is signed by the run that published it. The five releases before that date were signed by a run started by hand on `main`, so their provenance names the branch, not the tag; the archive is still the tag's tree, and the signature still belongs to this repository's workflow. The workflow that makes them is [`release-assets.yml`](.github/workflows/release-assets.yml).

---

## About the maintainer

Vladimir Mikhalev, Docker Captain, CNCF Ambassador, IBM Champion, AWS Community
Builder. Twenty years of production infrastructure.

- Website: [heyvaldemar.com](https://www.heyvaldemar.com/)
- GitHub: [@heyvaldemar](https://github.com/heyvaldemar)
