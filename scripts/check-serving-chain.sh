#!/usr/bin/env bash
# Triage "the site is unreachable from outside" in the right order:
# app -> Caddy -> TLS -> DNS. See docs/DEPLOYMENT_NETWORKING.md.
#
# Usage: ./scripts/check-serving-chain.sh [hostname]   (default: go.dresdell.com)
set -uo pipefail

HOST="${1:-go.dresdell.com}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

FLASK_RUN_PORT=5001
if [ -f "$ENV_FILE" ]; then
    port_line="$(grep -E '^FLASK_RUN_PORT=' "$ENV_FILE" | tail -1)"
    [ -n "$port_line" ] && FLASK_RUN_PORT="${port_line#FLASK_RUN_PORT=}"
fi

CADDY_CONTAINER="iptv-caddy"
pass=0
fail=0

ok()   { printf '  OK    %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }
info() { printf '        %s\n' "$1"; }

echo "[1/4] Flask app on 127.0.0.1:$FLASK_RUN_PORT"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$FLASK_RUN_PORT/" 2>/dev/null)"
if [ "$code" = "200" ]; then
    ok "http_code=$code"
else
    bad "http_code=${code:-none} -- app problem, stop here, do not touch DNS/Caddy/firewall"
fi
echo

echo "[2/4] Caddy container network mode ($CADDY_CONTAINER)"
netmode="$(docker inspect "$CADDY_CONTAINER" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null)"
if [ "$netmode" = "host" ]; then
    ok "NetworkMode=host (shares the host loopback -- 127.0.0.1 in the Caddyfile is correct)"
elif [ -n "$netmode" ]; then
    bad "NetworkMode=$netmode (not host -- the Caddyfile's 127.0.0.1 target may need to change)"
else
    bad "could not inspect container '$CADDY_CONTAINER' (not running, or wrong name?)"
fi
echo

echo "[3/4] HTTPS through Caddy, locally, bypassing DNS ($HOST)"
resp="$(curl -sk -o /dev/null -w '%{http_code}' -m 10 --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null)"
if [ "$resp" = "200" ]; then
    ok "http_code=$resp"
    cert_dates="$(echo | timeout 8 openssl s_client -connect 127.0.0.1:443 -servername "$HOST" 2>/dev/null \
        | openssl x509 -noout -dates 2>/dev/null)"
    [ -n "$cert_dates" ] && info "$cert_dates"
else
    bad "http_code=${resp:-none} -- Caddy/TLS problem, still not DNS"
fi
echo

echo "[4/4] Public DNS for $HOST"
this_ip="$(curl -s -m 5 https://api.ipify.org 2>/dev/null)"
info "this machine's public IP: ${this_ip:-unknown}"
dns_bad=0
for resolver in 1.1.1.1 8.8.8.8 9.9.9.9; do
    resolved="$(python3 - "$resolver" "$HOST" <<'PYEOF'
import socket, struct, random, sys
server, name = sys.argv[1], sys.argv[2]
tid = random.randint(0, 65535)
pkt = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
pkt += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
pkt += struct.pack("!HH", 1, 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(4)
try:
    s.sendto(pkt, (server, 53))
    data, _ = s.recvfrom(4096)
except Exception:
    print("TIMEOUT"); sys.exit(0)
finally:
    s.close()
rcode = data[3] & 0xF
if rcode == 3:
    print("NXDOMAIN"); sys.exit(0)
if rcode != 0:
    print("SERVFAIL"); sys.exit(0)
qd, an = struct.unpack("!HH", data[4:8])
off = 12
def skip_name(d, o):
    while True:
        l = d[o]
        if l == 0: return o + 1
        if l & 0xC0 == 0xC0: return o + 2
        o += 1 + l
for _ in range(qd):
    off = skip_name(data, off) + 4
for _ in range(an):
    off = skip_name(data, off)
    rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", data[off:off+10])
    off += 10
    if rtype == 1:
        print(socket.inet_ntoa(data[off:off+rdlen])); sys.exit(0)
    off += rdlen
print("NO-A-RECORD")
PYEOF
)"
    case "$resolved" in
        "$this_ip")
            info "$resolver -> $resolved (matches this machine)"
            ;;
        "66.96.162.131")
            info "$resolver -> $resolved  <-- STALE EIG WILDCARD"
            dns_bad=1
            ;;
        "66.110.156.88")
            info "$resolver -> $resolved  <-- STALE EIG RECORD"
            dns_bad=1
            ;;
        TIMEOUT|SERVFAIL|NXDOMAIN|NO-A-RECORD)
            info "$resolver -> $resolved"
            dns_bad=1
            ;;
        *)
            info "$resolver -> $resolved  <-- unexpected, does not match this machine"
            dns_bad=1
            ;;
    esac
done
if [ "$dns_bad" = "0" ]; then
    ok "all resolvers agree with this machine's public IP"
else
    bad "DNS mismatch -- see docs/DEPLOYMENT_NETWORKING.md 'Known incident: split-brain DNS'"
fi
echo

echo "===================================================================="
if [ "$fail" = "0" ]; then
    echo "VERDICT: full chain OK."
else
    echo "VERDICT: $fail check(s) failed, $pass passed."
    echo "  Fix the FIRST failing step only -- later steps can show false"
    echo "  failures once an earlier one is broken (e.g. DNS pointing"
    echo "  elsewhere makes step 3/4 irrelevant even if Caddy is fine)."
    echo "  If step 4 (DNS) is the only failure: this is a registrar-side"
    echo "  fix (Domain.com apex NS + deleting the stale EIG zone), NOT"
    echo "  something to fix by changing FLASK_RUN_HOST, Caddy, or the"
    echo "  firewall on this machine."
fi
echo "===================================================================="
