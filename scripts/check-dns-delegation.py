#!/usr/bin/env python3
"""Diagnose dresdell.com DNS delegation and per-subdomain record presence.

Companion to check-serving-chain.sh, which stops at "public DNS disagrees
with this machine". This script answers the next question: *why*, and *whose
zone* is answering.

There is no `dig` on this machine, and dnspython is not a project dependency,
so this speaks DNS over raw sockets with nothing but the stdlib.

Two distinct failure modes have bitten this domain, and they need different
fixes -- telling them apart is the whole point of this script:

  1. A subdomain has no A record in the correct (Domain.com) zone. It shows
     up as an authoritative NXDOMAIN there, and falls through to the stale
     EIG wildcard for anyone whose resolver follows the old in-zone NS.
     Fix: add the A record in the Domain.com panel.
  2. The apex NS RRset inside the zone points at the decommissioned EIG
     nameservers, which still serve `*.dresdell.com -> 66.96.162.131` (403).
     Fix: delete the zone in the Newfold/EIG account.

Complication worth knowing about: Domain.com's nameservers are anycast and
have been observed REFUSING this zone on most nodes while a minority serve
it correctly. A single query proves nothing -- so every authoritative lookup
here retries until it catches an AA (authoritative) answer.

Usage:
    python3 scripts/check-dns-delegation.py
    python3 scripts/check-dns-delegation.py --domain example.com --subs a,b,c
"""

import argparse
import collections
import socket
import struct
import random
import sys
import time

QTYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "DS": 43, "AAAA": 28}
TYPE_NAMES = {v: k for k, v in QTYPES.items()}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}

# a.gtld-servers.net -- the .com registry, for reading the parent delegation.
GTLD_SERVER = "192.5.6.30"

# The decommissioned Newfold/EIG zone. Any answer in here means the stale
# zone is winning.
EIG_IPS = {
    "66.96.162.131": "EIG wildcard (*.dresdell.com) -- serves 403",
    "66.110.156.88": "EIG stale tv record",
    "66.96.149.22": "EIG stale www record",
}
EIG_NS_MARKER = "yourhostingaccount.com"

PUBLIC_RESOLVERS = [
    ("8.8.8.8", "Google"),
    ("1.1.1.1", "Cloudflare"),
    ("9.9.9.9", "Quad9"),
    ("208.67.222.222", "OpenDNS"),
    ("64.6.64.6", "Verisign"),
]


# --------------------------------------------------------------------------
# Minimal DNS wire protocol
# --------------------------------------------------------------------------

def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def parse_name(data, off):
    """Decode a (possibly compressed) name. Returns (name, offset_after)."""
    labels = []
    jumped = False
    after = off
    while True:
        length = data[off]
        if length == 0:
            off += 1
            break
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack("!H", data[off:off + 2])[0] & 0x3FFF
            if not jumped:
                after = off + 2
                jumped = True
            off = pointer
            continue
        off += 1
        labels.append(data[off:off + length].decode("ascii", "replace"))
        off += length
    return ".".join(labels), (after if jumped else off)


def parse_record(data, off):
    name, off = parse_name(data, off)
    rtype, _rclass, ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
    off += 10
    rdata = data[off:off + rdlen]

    if rtype == 1:
        value = socket.inet_ntoa(rdata)
    elif rtype in (2, 5):
        value = parse_name(data, off)[0]
    elif rtype == 6:
        _mname, o2 = parse_name(data, off)
        _rname, o3 = parse_name(data, o2)
        value = "serial=%d" % struct.unpack("!I", data[o3:o3 + 4])[0]
    elif rtype == 43:
        keytag, alg, digest = struct.unpack("!HBB", rdata[:4])
        value = "keytag=%d alg=%d digest=%d" % (keytag, alg, digest)
    else:
        value = rdata.hex()[:32]

    return {"name": name, "type": TYPE_NAMES.get(rtype, str(rtype)),
            "ttl": ttl, "value": value}, off + rdlen


def query(server, name, qtype="A", recursive=False, timeout=4):
    """One DNS query. Returns a dict, or {'error': ...} on failure."""
    flags = 0x0100 if recursive else 0x0000
    packet = struct.pack("!HHHHHH", random.randint(0, 65535), flags, 1, 0, 0, 0)
    packet += encode_name(name) + struct.pack("!HH", QTYPES[qtype], 1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    except Exception as exc:  # noqa: BLE001 - any network failure is just "no answer"
        return {"error": str(exc)}
    finally:
        sock.close()

    _, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", data[:12])
    off = 12
    for _ in range(qdcount):
        _, off = parse_name(data, off)
        off += 4

    result = {
        "rcode": RCODES.get(flags & 0xF, str(flags & 0xF)),
        "authoritative": bool(flags & 0x0400),
        "answer": [], "authority": [], "additional": [],
    }
    for section, count in (("answer", ancount), ("authority", nscount),
                           ("additional", arcount)):
        for _ in range(count):
            try:
                record, off = parse_record(data, off)
                result[section].append(record)
            except Exception:  # noqa: BLE001 - truncated/unknown rdata, stop this section
                break
    return result


def query_until_authoritative(server, name, qtype, attempts=20, pause=0.35):
    """Retry past REFUSED/SERVFAIL until an authoritative answer appears.

    Domain.com's anycast fleet has served this zone on only a minority of
    nodes, so a single REFUSED is not evidence the record is missing.
    Returns (result_or_None, tries_used, Counter_of_rcodes).
    """
    tally = collections.Counter()
    for attempt in range(attempts):
        result = query(server, name, qtype)
        if "error" in result:
            tally["TIMEOUT"] += 1
        else:
            tally[result["rcode"]] += 1
            if result["authoritative"] and result["rcode"] in ("NOERROR", "NXDOMAIN"):
                return result, attempt + 1, tally
        time.sleep(pause)
    return None, attempts, tally


def resolve_host(hostname):
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(
            hostname, 53, socket.AF_INET, socket.SOCK_DGRAM)})
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def check_parent_delegation(domain):
    section("1. PARENT DELEGATION -- what the .com registry says")
    result = query(GTLD_SERVER, domain, "NS")
    if "error" in result:
        print("  could not reach the registry: %s" % result["error"])
        return []

    nameservers = sorted(r["value"] for r in result["authority"] if r["type"] == "NS")
    if not nameservers:
        print("  no delegation found (rcode=%s)" % result["rcode"])
        return []

    for ns in nameservers:
        print("  %s" % ns)
    if any(EIG_NS_MARKER in ns for ns in nameservers):
        print("  ^^ the registry itself delegates to the stale EIG servers.")
        print("     Fix this at the registrar before anything else.")
    else:
        print("  -> delegation looks correct")
    return nameservers


def check_dnssec(domain):
    section("2. DNSSEC -- is a validation failure in play?")
    result = query(GTLD_SERVER, domain, "DS")
    ds = [r for r in result.get("authority", []) + result.get("answer", [])
          if r["type"] == "DS"]
    if ds:
        for record in ds:
            print("  DS %s (ttl=%d)" % (record["value"], record["ttl"]))
        print("  -> DNSSEC is ON. A broken chain here shows up as SERVFAIL")
        print("     everywhere; re-test with CD=1 to confirm.")
    else:
        print("  no DS records at the parent -> DNSSEC is off, ruled out as a cause")


def check_zone_contents(label, servers, domain, subs, attempts=20):
    """Ask one nameserver set what it actually holds, record by record."""
    section("3. ZONE CONTENTS AT %s" % label)
    if not servers:
        print("  no reachable servers for this set")
        return {}

    server = servers[0]
    print("  querying %s (retrying past REFUSED to catch an authoritative answer)" % server)
    print()

    findings = {}
    apex, tries, tally = query_until_authoritative(server, domain, "SOA", attempts)
    if apex:
        serial = ", ".join(r["value"] for r in apex["answer"]) or "--"
        print("  %-28s %s  [%d tries]" % (domain + " SOA", serial, tries))
    else:
        print("  %-28s NO AUTHORITATIVE ANSWER in %d tries  %s"
              % (domain + " SOA", tries, dict(tally)))
        print("      ^^ REFUSED means these servers do not serve this zone at all.")

    for sub in subs:
        fqdn = "%s.%s" % (sub, domain)
        result, tries, tally = query_until_authoritative(server, fqdn, "A", attempts)
        if result is None:
            findings[sub] = "UNKNOWN"
            print("  %-28s NO AUTHORITATIVE ANSWER in %d tries  %s"
                  % (fqdn, tries, dict(tally)))
            continue

        addresses = [r["value"] for r in result["answer"] if r["type"] == "A"]
        if result["rcode"] == "NXDOMAIN" or not addresses:
            findings[sub] = "MISSING"
            print("  %-28s MISSING (no A record)  [%d tries]" % (fqdn, tries))
        else:
            findings[sub] = addresses[0]
            flag = ""
            if addresses[0] in EIG_IPS:
                flag = "  <-- %s" % EIG_IPS[addresses[0]]
            ttl = [r["ttl"] for r in result["answer"] if r["type"] == "A"][0]
            print("  %-28s %s (ttl=%d)%s" % (fqdn, addresses[0], ttl, flag))
    return findings


def check_public_resolvers(domain, subs, expected_ip):
    """Poll recursive resolvers. Returns {sub: Counter of observed outcomes}."""
    section("4. WHAT THE WORLD SEES (recursive public resolvers)")
    header = "  %-12s" % "resolver" + "".join("%-18s" % s for s in subs)
    print(header)
    print("  " + "-" * (len(header) - 2))

    observed = {sub: collections.Counter() for sub in subs}
    for ip, name in PUBLIC_RESOLVERS:
        row = "  %-12s" % name
        for sub in subs:
            result = query(ip, "%s.%s" % (sub, domain), "A", recursive=True)
            if "error" in result:
                cell = "TIMEOUT"
            else:
                addresses = [r["value"] for r in result["answer"] if r["type"] == "A"]
                if not addresses:
                    cell = result["rcode"]
                elif addresses[0] == expected_ip:
                    cell = "OK"
                elif addresses[0] in EIG_IPS:
                    cell = "EIG %s" % addresses[0]
                else:
                    cell = addresses[0]
            observed[sub][cell] += 1
            row += "%-18s" % cell
        print(row)
        time.sleep(0.15)

    print()
    print("  apex NS as cached by each resolver:")
    for ip, name in PUBLIC_RESOLVERS:
        result = query(ip, domain, "NS", recursive=True)
        if "error" in result:
            print("    %-12s TIMEOUT" % name)
            continue
        values = sorted(r["value"] for r in result["answer"] if r["type"] == "NS")
        if not values:
            print("    %-12s %s" % (name, result["rcode"]))
        elif any(EIG_NS_MARKER in v for v in values):
            print("    %-12s STALE EIG  %s" % (name, " ".join(values)))
        else:
            print("    %-12s ok         %s" % (name, " ".join(values)))
        time.sleep(0.15)
    return observed


def verdict(findings, expected_ip, subs, observed=None):
    section("VERDICT")
    observed = observed or {}

    missing = [s for s in subs if findings.get(s) == "MISSING"]
    unknown = [s for s in subs if findings.get(s) == "UNKNOWN"]
    wrong = [s for s in subs
             if findings.get(s) not in ("MISSING", "UNKNOWN", None, expected_ip)]

    # When the delegated servers refused every attempt we cannot read the zone
    # directly -- but a recursive resolver reporting NXDOMAIN got that from an
    # authoritative source, so it settles the question just as well. Promote
    # those from "undetermined" to "missing" rather than reporting a shrug.
    promoted = []
    for sub in list(unknown):
        if observed.get(sub, {}).get("NXDOMAIN", 0) >= 2:
            unknown.remove(sub)
            missing.append(sub)
            promoted.append(sub)
    if promoted:
        print("  Note: %s could not be read directly (servers refused), but"
              % ", ".join(promoted))
        print("  multiple public resolvers returned an authoritative NXDOMAIN,")
        print("  which is conclusive: the record does not exist.")
        print()

    if missing:
        print("  MISSING A RECORD in the correct zone: %s" % ", ".join(missing))
        print("    -> Add in the Domain.com panel: host '%s', type A," % missing[0])
        print("       value %s, TTL 900. Do NOT add a wildcard/catch-all;" % expected_ip)
        print("       an explicit record per host is the correct fix.")
    if wrong:
        print("  WRONG ADDRESS: %s" % ", ".join("%s=%s" % (s, findings[s]) for s in wrong))
    if unknown:
        print("  UNDETERMINED (nameservers refused every attempt): %s" % ", ".join(unknown))
        print("    -> The zone is not reliably loaded on the delegated servers.")
        print("       This is a provider-side problem, not a record problem.")
    if not (missing or wrong or unknown):
        print("  All checked records resolve to %s in the authoritative zone." % expected_ip)

    print()
    print("  Reminder: none of this is fixable on this machine. Do not change")
    print("  FLASK_RUN_HOST, Caddy, or the firewall. And do not trust a curl")
    print("  from this box to its own public IP -- the router hairpins it.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", default="dresdell.com")
    parser.add_argument("--subs", default="go,tv,crm,live,ai",
                        help="comma-separated subdomains to check")
    parser.add_argument("--attempts", type=int, default=20,
                        help="retries per authoritative lookup (default 20)")
    args = parser.parse_args()

    subs = [s.strip() for s in args.subs.split(",") if s.strip()]

    print("DNS delegation report for %s" % args.domain)

    # Read this machine's public IP the same way check-serving-chain.sh does.
    expected_ip = None
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=6) as resp:
            expected_ip = resp.read().decode().strip()
    except Exception:  # noqa: BLE001
        pass
    print("this machine's public IP: %s" % (expected_ip or "unknown"))

    parent_ns = check_parent_delegation(args.domain)
    check_dnssec(args.domain)

    good_servers = []
    for ns in parent_ns:
        good_servers += resolve_host(ns)
    findings = check_zone_contents("THE DELEGATED NAMESERVERS", good_servers,
                                   args.domain, subs, args.attempts)

    eig_servers = resolve_host("ns1." + EIG_NS_MARKER)
    if eig_servers:
        # The stale zone always answers on the first try, so don't waste retries.
        check_zone_contents("THE STALE EIG NAMESERVERS", eig_servers,
                            args.domain, subs, attempts=3)

    observed = check_public_resolvers(args.domain, subs, expected_ip)
    verdict(findings, expected_ip, subs, observed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
