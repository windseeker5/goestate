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

- **Domain.com** (correct, matches the `.com` registry delegation) — holds the
  real `A` records → this machine's public IP. **Which hosts it actually
  covers has to be checked, not assumed** — see below.
- **Newfold/EIG** (`yourhostingaccount.com`, an old host, should have been
  decommissioned) — still live, with a **wildcard** record: any
  `*.dresdell.com` name not otherwise listed resolves to a dead parking IP,
  which serves a `403`.

Run the diagnostic rather than reasoning about it from memory:

```bash
python3 scripts/check-dns-delegation.py
```

It reads the parent delegation, both nameserver sets, and several public
resolvers, and tells you which of the three failure modes below you have.
(It speaks DNS over raw sockets — `dig` is not installed on this machine.)

There are **three distinct failures**, and they need different fixes. Do not
stop at the first one you recognize:

1. **A host is simply missing from the good zone.** Confirmed on 2026-08-18:
   `go`, `www` and `ai` had *no* `A` record at Domain.com, while `tv`, `crm`
   and `live` had a correct one (`75.158.253.160`, ttl 900). A missing host
   falls through to the EIG wildcard → 403, which makes an absent record look
   like a routing or proxy problem. Fix: add the `A` record. **Do not add a
   wildcard/catch-all to "solve" it** — an explicit record per host is
   correct, and the only wildcard in play is the EIG one that causes the 403.
2. **The apex `NS` RRset inside the zone points at the EIG nameservers**
   instead of Domain.com's. Resolvers follow the in-zone NS (it outranks the
   parent referral), ask EIG, and hit the wildcard. This is per-hostname,
   per-resolver, and re-rolls on every cache expiry — so it looks like it
   "only affects one subdomain" when all of them are equally exposed.
3. **The delegated nameservers refuse the zone.** Observed 2026-08-18:
   `ns1/ns2/ns3.domain.com` returned `REFUSED` on the large majority of
   queries, with only a minority of anycast nodes serving the zone at all.
   `REFUSED` from an authoritative server means "I don't host this zone."
   This is why hosts with perfectly good records still SERVFAIL worldwide,
   and it is purely provider-side.

**Fingerprints** (fast way to recognize each again):
- Public DNS resolves a `*.dresdell.com` name to `66.96.162.131` (EIG
  wildcard), `66.110.156.88` (stale `tv`) or `66.96.149.22` (stale `www`)
  instead of this machine's real IP → failure 1 or 2.
- An authoritative `NXDOMAIN` for one host while sibling hosts answer
  normally → failure 1, that host's record is missing.
- `dresdell.com`'s apex `NS` answer mentions `yourhostingaccount.com` instead
  of `domain.com` → failure 2.
- `REFUSED` from `ns*.domain.com`, or widespread `SERVFAIL` at public
  resolvers for hosts you know have records → failure 3.
- The failure seems to move between subdomains over time with no config
  change on this machine → failure 2 or 3.

Because a single query proves nothing while failure 3 is active, check each
host individually and retry until you catch an authoritative (`AA`) answer —
that is exactly what `check-dns-delegation.py` does. A recursive resolver
returning `NXDOMAIN` is also conclusive on its own: it got that from an
authoritative source.

**Not DNSSEC.** The `.com` parent publishes no `DS` records for this domain,
and `CD=1` changes nothing — so widespread `SERVFAIL` here is never a
validation failure. Re-confirm with the script rather than re-testing by hand.

**Fix** (registrar control panels, not this repo):
1. Domain.com → DNS/zone editor for `dresdell.com` → add the missing `A`
   records (host `go`, value = this machine's public IP, TTL 900), and set the
   apex NS records to `ns1.domain.com` / `ns2.domain.com` / `ns3.domain.com`.
2. Newfold/EIG account → delete the `dresdell.com` zone entirely. Until this
   is done, every subdomain stays exposed to the wildcard on cache re-roll.
3. Raise the `REFUSED` unreliability with the same provider — Domain.com and
   EIG are both Newfold, so it is one vendor and one ticket.
4. Wait ~1 hour (old records carry a 3600s TTL) and re-verify with the script.

**Why this was previously misdiagnosed** as a Gunicorn→`python app.py`
regression, a `FLASK_RUN_HOST` bind issue, and a firewall issue: the switch to
`python app.py` happened to coincide with a DNS cache re-roll, and the local
chain returning `200` was never actually checked first.

**And why it was misdiagnosed a fourth time, on 2026-08-18:** this document
previously asserted that the Domain.com zone had good `A` records for
`go`/`tv`/`crm`/`live`/`ai`. That was taken on faith and was wrong — `go` had
no record at all. Treating the zone as uniformly correct hid a one-line fix
behind a much scarier-looking delegation story. Verify per host; do not trust
this file's record list, including the one above.

Two traps specific to this machine, both of which have produced false
"it works" / "it's broken" readings:

- `/etc/hosts` pins `tv.dresdell.com` to a LAN address (`192.168.1.89`), so
  local `tv` results say nothing about public DNS.
- The router hairpins this machine's own public IP, so `curl` from here to
  `75.158.253.160` returns 200 even when the outside world cannot reach it.
  Verify from off-LAN, or with the DoH one-liner in the section above.
