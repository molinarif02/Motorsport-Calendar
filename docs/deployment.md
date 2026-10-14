# Deployment

The stack is two containers (plus an optional tunnel), sized for a Raspberry Pi 4/5.
It also runs on any Docker or Podman host.

| service | image | role | memory cap |
|---|---|---|---|
| `generator` | `ghcr.io/molinarif02/motorsport-calendar` (or built locally) | runs the pipeline in a loop | 256 MB |
| `web` | `nginx:1.27-alpine` | serves `/motorsport.ics` and `/status.json` on `:8080` | 32 MB |
| `tunnel` (profile) | `cloudflare/cloudflared` | optional public HTTPS without opening ports | 64 MB |

Volumes:

- `data`: the generator's state. Back it up if you like, but it is safe to lose (see
  [architecture.md](architecture.md#persistent-state-data)).
- `www`: the published files.
- `./config`: your configuration, mounted read-only.

## Install

```bash
git clone https://github.com/molinarif02/Motorsport-Calendar.git
cd Motorsport-Calendar
cp .env.example .env                                  # YOUTUBE_API_KEY, TUNNEL_TOKEN (both optional)
$EDITOR config/config.yaml                            # at least: contact, FIAWEC+ link
docker compose pull generator || docker compose build generator
docker compose run --rm generator validate
```

### First run and check-up

Do a single run in the foreground and read the summary:

```bash
docker compose run --rm generator run
docker compose run --rm --entrypoint cat generator /www/status.json
```

Every collector should show `"ok": true` with a plausible `sessions` count. If one
doesn't, the `errors` list says why; see [collectors.md](collectors.md) for each
source's caveats. Then start the stack:

```bash
docker compose up -d
docker compose logs -f generator
curl -sI localhost:8080/motorsport.ics          # 200, Content-Type: text/calendar
```

### Image: prebuilt or local

- **Prebuilt.** GitHub Actions publishes a multi-arch image (amd64 and arm64) to
  GHCR on every push to `main`. GHCR packages start out **private**. Either make the
  package public (GitHub → your profile → Packages → motorsport-calendar → Package
  settings → Change visibility), or log the Pi in with
  `docker login ghcr.io -u molinarif02` and a token that has `read:packages`.
- **Local build.** `docker compose build generator` works on the Pi itself. All
  dependencies ship arm64 wheels, so nothing is compiled; it takes a few minutes.

## Public HTTPS

iCloud and Proton download the feed **from their own servers**, so it must be
reachable from the internet over HTTPS. Only two paths are served, and everything
else returns 404.

**Option A: Cloudflare Tunnel** (no port forwarding, no certificates to manage).

1. In Cloudflare Zero Trust, go to *Networks → Tunnels → Create tunnel*
   (cloudflared) and copy the token.
2. Add a public hostname, e.g. `calendar.example.com`, with service `http://web:8080`.
3. Put `TUNNEL_TOKEN=...` in `.env`, then run `docker compose --profile tunnel up -d`.

**Option B: your existing reverse proxy** (Caddy, Traefik, Nginx Proxy Manager…).
Point it at `<pi>:8080`. For example, with Caddy:

```
calendar.example.com {
    reverse_proxy 192.168.1.10:8080
}
```

Your subscription URL is then `https://calendar.example.com/motorsport.ics`.

## Updating

```bash
git pull
docker compose pull && docker compose up -d     # or: docker compose up -d --build
```

Your `config/` edits survive `git pull` unless the same lines changed upstream; git
tells you if they did. The calendar keeps its UIDs across updates, so subscribers see
no duplicates.

## Monitoring

- The container health check fails if `status.json` hasn't been rewritten in 7 hours
  (`docker ps` shows `unhealthy`).
- `https://<host>/status.json` reports per-collector health, and suits an Uptime Kuma
  JSON-query monitor, for example on `collectors.wrc.ok == true`.
- Logs: `docker compose logs generator`. They are rotated at 2 × 2 MB.

## Without Docker

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m mscal run --loop --config config/config.yaml --data ./data --out ./www
```

Serve `./www` with any web server, and run the command under systemd or similar.
