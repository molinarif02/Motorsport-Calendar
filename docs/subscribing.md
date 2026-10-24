# Subscribing

Use the feed's public URL everywhere: `https://<your-host>/motorsport.ics`. Or, to get
one calendar per championship (its own colour, and hideable with one tap), subscribe
to the files under `https://<your-host>/calendars/` instead, e.g. `…/calendars/wec.ics`.
The status page lists them, each with a Subscribe button. Use one or the other, not
both, or every event appears twice. Some apps
prefer the `webcal://<your-host>/motorsport.ics` form, which is the same feed.

A subscribed calendar is **read-only** and refreshed by the client on its own
schedule. Every subscriber sees the same calendar, and you can share the link with
anyone.

## iCloud (Apple Calendar)

Store the subscription **in iCloud**. It then appears on your iPhone, iPad, Mac and
iCloud.com, and stays updated on all of them. Subscribe once, from one device:

- **iPhone / iPad**: Calendar → Calendars → Add Calendar → **Add Subscription
  Calendar** → paste the URL → set **Account: iCloud** → Done. (Older iOS versions:
  Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar, which
  keeps it on that device only.)
- **Mac**: Calendar → File → **New Calendar Subscription…** → paste the URL → set
  **Location: iCloud**. Set **Auto-refresh** to every 15 minutes or every hour. If you
  enabled `alarm_minutes`, **untick "Remove: Alerts"**.

The location can't be changed later. Don't subscribe twice, or you'll see every
event twice.

With the subscription in iCloud, the feed is fetched from outside your network, so
the URL must be **publicly reachable** (for example through the Cloudflare tunnel, see
[deployment.md](deployment.md)). A LAN address like `http://raspberrypi:8080` only works
for a subscription kept "On My iPhone", and only while you're at home.

The quickest way: open `https://<your-host>/status.html` on the device and tap
**Subscribe**.

## Proton Calendar

Calendar (web) → *Other calendars* **+** → **Add calendar from URL** → paste the URL.
Proton refreshes subscribed calendars on its own schedule. If the option is missing,
check what your plan includes.

## Google Calendar

On calendar.google.com, open *Other calendars* **+** → **From URL** → paste the URL.
Google refreshes external calendars only every several hours, and you can't change
that.

## Outlook

Outlook.com: *Add calendar → Subscribe from web*. Outlook desktop: *Add Calendar →
From Internet*.

## Thunderbird

*New Calendar → On the Network → Location:* paste the URL. You can set the refresh
interval in the calendar's properties.

## What you'll see

- Titles: `[SERIES] [Session type] Session – Event`, for example
  `[WEC] [Race] Race – 8 Hours of Bahrain`. Search for `[WRC]` or `[Race]` to filter.
- Times: shown in your own timezone. The description also gives the local track time.
- Location: the circuit, or for WRC the stage start, with a map pin in Apple Calendar.
- URL: the live stream (or the best available streaming service).
- Categories: series, championship, race type, session type and circuit label. These
  show in Apple, Outlook and Thunderbird.
- Cancelled sessions: prefixed with `CANCELLED`.
