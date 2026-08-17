# Deployment Networking

How Estate Copilot (`go.dresdell.com`) is actually served in this
environment, and how to triage "the site is unreachable" without guessing.

## Topology

```
internet
  -> Caddy (container "iptv-caddy", network_mode: host, listens :80/:443)
     Caddyfile: /home/kdresdell/Documents/DEV/iptv/infrastructure/Caddyfile
     (lives outside this repo — it's shared with the other *.dresdell.com
     services, e.g. tv/crm/live/ai)
  -> reverse_proxy 127.0.0.1:5001
  -> python app.py (Flask dev server, FLASK_RUN_HOST=127.0.0.1)
```

Caddy runs with `network_mode: host`, not the default bridge network. That
means Caddy shares the host's network namespace — `127.0.0.1` inside the Caddy
container *is* the host's loopback, the same one Flask binds to. This is why
`FLASK_RUN_HOST=127.0.0.1` is correct and sufficient here; `0.0.0.0` would only
matter for a *bridge*-networked proxy, and would needlessly expose the
Werkzeug debugger to the LAN in the meantime (see `CLAUDE.md`).

Verify the network mode before assuming otherwise:

```bash
docker inspect iptv-caddy --format '{{.HostConfig.NetworkMode}}'   # -> host
```

## Triage order

Cheapest and most decisive checks first. **Do not touch `FLASK_RUN_HOST`,
Caddy config, the router, or the firewall until step 1 has failed** — every
one of those has been the wrong target in a past incident (see below).

```bash
./scripts/check-serving-chain.sh go.dresdell.com
```

Or by hand:

1. **App itself:** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/`
   Expect `200`. If this fails, it's an app problem — nothing below matters yet.
2. **Caddy's network mode:** confirm `host` as above. If it's `bridge`, the
   `127.0.0.1` target in the Caddyfile would need to change — but that hasn't
   been true in this environment.
3. **Through Caddy, locally, bypassing DNS:**
   ```bash
   curl -sk -o /dev/null -w '%{http_code}\n' \
     --resolve go.dresdell.com:443:127.0.0.1 https://go.dresdell.com/
   ```
   Expect `200` with a valid cert. This proves Caddy + TLS + the app are all
   fine *before* DNS or the internet are involved at all.
4. **Public DNS**, only once 1–3 all pass:
   ```bash
   python3 - <<'EOF'
   import socket
   print(socket.gethostbyname('go.dresdell.com'))
   EOF
   ```
   Compare against this machine's actual public IP (`curl -s https://api.ipify.org`).
   A mismatch here — not the app, not Caddy — is the bug. See below.

**The trap:** curling this machine's own public IP (e.g. `75.158.253.160`)
*from this machine* is hairpinned by the router and will return 200 even when
the outside world can't reach it. That proves nothing. Verify from off-LAN: a
phone on cellular data, or https://check-host.net.

## Known incident: split-brain DNS on `dresdell.com`

As of 2026-08, `dresdell.com` has two providers with live records:

- **Domain.com** (correct, matches the `.com` registry delegation) — has good
  `A` records for `go`/`tv`/`crm`/`live`/`ai` → this machine's public IP.
- **Newfold/EIG** (`yourhostingaccount.com`, an old host, should have been
  decommissioned) — still live, with a **wildcard** record: any
  `*.dresdell.com` name not otherwise listed resolves to a dead parking IP,
  which serves a `403`.

The bug: the Domain.com zone's own apex `NS` records incorrectly point at the
EIG nameservers instead of Domain.com's. Resolvers that follow the in-zone NS
(which outranks the parent referral) end up asking EIG, and hit the wildcard.
This is **per-hostname, per-resolver, and re-rolls on every cache expiry** —
so it looks like it "only affects one subdomain" when in fact all of them are
equally exposed. Whichever name you happen to query least often is most
likely to be the one currently showing broken, because its cache entry is the
one most often expired and up for a bad re-roll.

**Fingerprints** (fast way to recognize this again):
- Public DNS resolves a `*.dresdell.com` name to `66.96.162.131` (EIG
  wildcard) or `66.110.156.88` (a stale hardcoded EIG record) instead of this
  machine's real IP.
- `dresdell.com`'s apex `NS` answer mentions `yourhostingaccount.com` instead
  of `domain.com`.
- The failure seems to move between subdomains over time with no config
  change on this machine.

**Fix** (registrar control panels, not this repo):
1. Domain.com → DNS/zone editor for `dresdell.com` → apex NS records → change
   to `ns1.domain.com` / `ns2.domain.com` / `ns3.domain.com`.
2. Newfold/EIG account → delete the `dresdell.com` zone entirely.
3. Wait ~1 hour (old records carry a 3600s TTL) and re-verify.

**Why this was previously misdiagnosed** as a Gunicorn→`python app.py`
regression, a `FLASK_RUN_HOST` bind issue, and a firewall issue: the switch to
`python app.py` happened to coincide with a DNS cache re-roll, and the local
chain returning `200` was never actually checked first.
