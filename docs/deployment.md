# Deployment

The stack is two containers (plus an optional tunnel), sized for a Raspberry Pi 4/5.
It also runs on any Docker or Podman host.

| service | image | role | memory cap |
|---|---|---|---|
| `generator` | `ghcr.io/molinarif02/motorsport-calendar` (or built locally) | ~15 MB idle supervisor; a short-lived run process (~55–80 MB) every 30 min–3 h | 256 MB cap |
| `web` | `nginx:1.30-alpine` | serves `/motorsport.ics`, `/status.json` and `/status.html` on `:8080` | 32 MB |
| `tunnel` (profile) | `cloudflare/cloudflared:2026.10.0` | optional public HTTPS without opening ports | 64 MB |

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
$EDITOR config/config.yaml                            # at least: contact
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
   (cloudflared). The install page shows a command like
   `docker run cloudflare/cloudflared:latest tunnel --no-autoupdate run --token eyJh…`.
   Copy **only the token**: the long string after `--token`, starting with `eyJ`. You
   don't run that command; the `tunnel` service in `docker-compose.yml` does the same.
2. Add a public hostname, e.g. `calendar.example.com`, with service type **HTTP** and
   URL `web:8080` (the nginx service, reached by name on the compose network).
3. Put `TUNNEL_TOKEN=eyJh…` in `.env` (one line, no quotes), then run
   `docker compose --profile tunnel up -d`.

If the `tunnel` container keeps restarting with "Provided Tunnel token is not valid",
the value in `.env` isn't the bare token. Check it without printing it:

```bash
python3 scripts/check-tunnel-token.py     # run next to docker-compose.yml
```

It reports a pasted `--token` or whole command, spaces, a Windows line ending, or
duplicate `TUNNEL_TOKEN` lines, and prints the tunnel id when the token is valid. Then
recreate the container so it reads the new value:
`docker compose --profile tunnel up -d --force-recreate tunnel`.

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
docker compose up -d --force-recreate web       # picks up a changed nginx.conf
```

`nginx.conf` is bind-mounted from the repo, and `docker compose up -d` doesn't recreate
`web` when only that file changed. A single-file bind mount also keeps pointing at the
old file after `git pull` replaces it. Recreating `web` takes a second, and is harmless
when nothing changed.

Keep your own configuration in `config.local/`, which git ignores, so `git pull` never
touches it:

```bash
mkdir -p config.local && cp config/config.yaml config/manual.yaml config.local/
echo "CONFIG_DIR=./config.local" >> .env
docker compose up -d
```

`config/` then stays the shipped example, which is useful for spotting new options
after an update (`diff config/config.yaml config.local/config.yaml`). The calendar keeps its UIDs across updates, so subscribers see
no duplicates.

## Monitoring

- The container health check fails if `status.json` hasn't been rewritten in 7 hours
  (`docker ps` shows `unhealthy`).
- `https://<host>/status.html` (or `/status`) shows the same report as a page: last and
  next run, events, and each source's state with its errors. It has a Subscribe button
  too. It is published by the generator, and refreshes every minute.
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
