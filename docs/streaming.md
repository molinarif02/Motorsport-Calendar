# Streaming links

Each event's `URL` (and the "Watch:" line of its description) points to where you
can watch it. Links are chosen in `mscal/streams.py` after collection.

## Rules

Under `streaming.series`, each series tag has an ordered list of rules. For every
session without a link, the first rule that matches **and** yields a link wins.

```yaml
streaming:
  series:
    GTWCE:
      - match: {session_types: [RACE, QUALI]}   # optional filter
        youtube: "@GTWorld"                      # exact live video, if found
        label: GT World (YouTube)
      - url: https://www.youtube.com/@GTWorld/streams   # otherwise: fixed link
        label: GT World (YouTube)
```

| key | |
|---|---|
| `match.session_types` | e.g. `[RACE, QUALI]` |
| `match.name_regex` | regex on the session name (`FP1`, `Hyperpole`, …) |
| `match.event_regex` | regex on the event name (`Le Mans`, …) |
| `url` + `label` | fixed link |
| `youtube` + `label` | channel handle (`@name`) or channel id (`UC…`) to search for the live video |

A link set by a collector itself (for example `stream_url` in `manual.yaml`) is never
overridden.

## Defaults shipped in `config.yaml`

| series | link |
|---|---|
| WEC | FIAWEC+. FP1/FP2 are labelled "live timing; video from FP3", because the platform streams video from FP3 onward. Paste your FIAWEC+ link into the two `url:` entries. |
| WRC | Rally.TV |
| GTWCE, GT Open, 24H Series, Drift Masters, Formula Drift | the series' YouTube channel. **Verify the handles**; they were not checked against live YouTube. |

## How YouTube matching works

Channels create a "scheduled live" video a few days before an event. To find it with
almost no API quota:

1. The channel handle is resolved to a channel id once (`channels.list`, 1 unit), and
   the result is cached forever.
2. The channel's public RSS feed lists its latest uploads. This costs no quota.
3. One `videos.list` call (1 unit for up to 50 videos) returns their scheduled start
   times.
4. The video whose scheduled start falls within `match_before_minutes` before to
   `match_after_minutes` after the session start is chosen. If several qualify,
   overlap with the session or event name decides, then closeness in time.
5. The match is cached per session forever, so after the race it keeps working as
   the replay link.

Lookups only happen for sessions starting within `youtube.lookahead_days`. The total
is about one unit per channel per run, against a free quota of 10,000 per day.

## Getting a YouTube API key

1. In <https://console.cloud.google.com/>, create a project.
2. Go to *APIs & Services → Library*, find **YouTube Data API v3**, and enable it.
3. Go to *Credentials → Create credentials → API key*. Restrict the key to the
   YouTube Data API v3.
4. Put the key in `.env` next to `docker-compose.yml`: `YOUTUBE_API_KEY=...`, then run
   `docker compose up -d`.

Without a key, `youtube:` rules are skipped and the next rule is used, which is
normally the channel's `/streams` page.
