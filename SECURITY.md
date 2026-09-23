# Security Policy

## Supported versions

| Version                                        | Status             |
|------------------------------------------------|--------------------|
| Current `main` and the latest tagged release   | :white_check_mark: |
| Older tags without a recent rebuild            | :x:                |

Fixes land on `main` and ship as a new tag; older tags are not patched in place.

## Reporting a vulnerability

Send reports to v@valdemar.ai. Encrypted email is preferred; the PGP public key is published at [heyvaldemar.com/security](https://heyvaldemar.com/security).

You can expect an acknowledgment within 7 days. This project does not operate a bounty program; researchers who submit valid, responsibly disclosed reports receive public credit in the release notes and the changelog.

Please do not open public GitHub issues for security reports.

## Supply chain trust

This repository publishes a deployment template, not a custom software distribution. Upstream images are pinned by `tag@sha256:<digest>` as interpolation defaults in the compose file's `x-images` block, so a plain `git pull` delivers the exact combination this repository has tested. The Pin Freshness workflow re-resolves every pin daily and fails when one has drifted; the Deployment Verification workflow boots the full stack on every change and fails when it breaks. Either one notifies the maintainer. GitHub Actions are pinned by commit SHA.

The README's "Supply chain trust" section lists the upstream images and where they come from.

## Credentials

`.env` is gitignored and required variables fail fast at deploy time.

## What this template is for, and what it is not

It fences an internet-facing endpoint off from a privileged one. It does not
make the privileged side safe: the worker holds a Docker socket, which is root
on the host, and the point of the arrangement is that nothing reachable from
outside can talk to it.

Two settings decide how much a click can do, and both start empty and refusing:
`ALLOWED_USER_IDS` says who may act, and the action table in `worker/actions.py`
says what may be done. A request names an action; it never carries a command.
Widen either one and you have widened what a stolen chat account is worth.
