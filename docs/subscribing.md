# Subscribing

Use the feed's public URL everywhere: `https://<your-host>/motorsport.ics`. Some apps
prefer the `webcal://<your-host>/motorsport.ics` form, which is the same feed.

A subscribed calendar is **read-only** and refreshed by the client on its own
schedule. Every subscriber sees the same calendar, and you can share the link with
anyone.

## iCloud (Apple Calendar)

Subscribe **once, from a Mac**. This is the only place where you choose both where
the subscription lives and how often it refreshes.

1. Calendar → File → **New Calendar Subscription…** → paste the URL → Subscribe.
2. Set **Location: iCloud**, so it appears on your iPhone, iPad and iCloud.com. You
   can't change this later.
3. Set **Auto-refresh** to every 15 minutes or every hour.
4. If you enabled `alarm_minutes`, **untick "Remove: Alerts"**.

With only an iPhone or iPad: Settings → Calendar → Accounts → Add Account → Other →
**Add Subscribed Calendar**. This works, but you can't pick the refresh interval, and
the subscription stays on that device only.

Don't subscribe both ways, or you'll see every event twice.

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
