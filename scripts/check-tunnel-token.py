#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check TUNNEL_TOKEN in .env without printing it.

Run next to docker-compose.yml:  python3 scripts/check-tunnel-token.py
A Cloudflare tunnel token is base64 JSON {"a": account, "t": tunnel id, "s": secret};
cloudflared says "Provided Tunnel token is not valid" when it can't decode it.
"""
import base64
import json
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else ".env"
vals = re.findall(r'^[ \t]*TUNNEL_TOKEN[ \t]*=([^\n]*)', open(path, newline='').read(), re.M)
v = vals[-1] if vals else ''        # docker compose uses the last one
print(f"TUNNEL_TOKEN lines: {len(vals)} | length: {len(v)} | starts with: {v[:4]!r}")
for bad, why in (('\r', 'Windows line ending (CR)'), ('--token', 'contains "--token"'), (' ', 'contains spaces'),
                 ('docker', 'contains the docker command')):
    if bad in v:
        print("  problem:", why)
try:
    d = json.loads(base64.b64decode(v.strip().strip('"\'') + '=='))
    print("  OK: valid token for tunnel", d['t'])
except Exception:
    print("  NOT a valid token: paste only the eyJ... string after --token")
