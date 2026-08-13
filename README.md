# Deck Essentials for Spotify

Spotify control for [StreamController](https://github.com/StreamController/StreamController) on Linux, built for
Stream Deck keys **and** Stream Deck+ dials.

Transport, seek, shuffle and repeat, volume and mute, Liked Songs, playlists, device transfer, a one-key
now-playing **Song Stack**, and five dial actions designed for the Stream Deck+ touchscreen strip rather than
enlarged from key art.

> **Disclaimer:** This plugin was built with the assistance of AI tools. Review
> the code yourself before relying on it, and please open an issue if you spot
> a bug or a mistake.


> Not affiliated with, endorsed by, or sponsored by Spotify. Spotify is a trademark of Spotify AB.

---

## Requirements

- **Spotify Premium.** Spotify's playback-control endpoints refuse commands from free accounts.
- **Your own Spotify Client ID** (free to create; no client secret is ever needed).
- StreamController 1.5.0-beta.8 or newer.

## Setting up

1. Install the plugin, then open **Settings → Plugins → Deck Essentials for Spotify**.
2. Copy the **Redirect URI** shown there — it looks like `http://127.0.0.1:8888/callback`.
3. Go to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) → **Create app**.
   Give it any name, paste the Redirect URI in, and save.
4. Copy the app's **Client ID** back into the plugin settings.
5. Press **Authenticate**. Your browser opens, you approve, and the tab tells you it worked.

That is the whole setup. Tokens are stored in a private file with user-only permissions, never in a page,
so exporting or sharing a page never carries your credentials with it.

## Actions

**Playback** — Play / Pause · Previous Song · Next Song · Backward Seek · Forward Seek

Previous and Next change track on a press and seek while held, at a configurable step and repeat rate.

**Modes** — Shuffle · Loop Context · Loop Song · Mode Stack

Mode Stack puts all three states on one key and shows them all at once, whatever the press and hold are mapped to.
Shuffle and Mode Stack both show Spotify's **smart shuffle** as a state of its own rather than as plain shuffle.

**Library** — Like / Unlike · Add to Playlist · Explicit Indicator

Liked state is looked up when the track changes, not on a timer, and says "unknown" rather than guessing.

**Volume** — Volume Up · Volume Down · Mute / Unmute · Set Volume · Volume Stack

Mute remembers the level it muted from, per device. Devices that report no volume support say so instead of
sending commands into the void.

**Now playing** — Song Stack

Album art, title, artist, transport state, progress, liked and explicit markers in one key, with a
configurable short press and hold. Artwork is shown uncropped in its own area, never underneath text.

**Utility** — Setup · Song Clipboard · Context Information · Transfer Playback · User Information · Play Context

**Stream Deck+ dials** — Playback Control · Volume Control · My Playlists · Queue Picker · Song Picker

| Dial | Rotate | Press | Tap | Long tap |
| --- | --- | --- | --- | --- |
| Playback Control | Previous / Next (hold the dial in to seek instead) | Play / Pause | Play / Pause | Like / Unlike |
| Volume Control | Volume | Mute while held | Mute / Unmute | — |
| My Playlists | Browse | Play | Play | Refresh |
| Queue Picker | Browse what is queued | Jump to it | Jump to it | Refresh |
| Song Picker | Browse what is playing, or a collection you link | Play in context | Play in context | Add to queue |

Every mapping above is a default you can change in the action's settings.

## How it behaves

- **One poll for the whole deck.** A single request per second covers every action on the page, and it stops
  entirely when no Spotify action is visible.
- **Instant feedback.** Pressing a key updates what the deck shows immediately while the request is still in
  flight, then reconciles with whatever Spotify actually reports.
- **Fast dial spins stay fast.** Rapid volume or seek changes are coalesced into the newest value, which is
  always sent — a five-click spin is one request, not five.
- **External changes show up.** Pause from your phone and the deck follows, without you touching it.
- **Failures are visible.** No device, rate limited, offline, restricted device, missing playlist and
  authentication problems each say what they are rather than failing silently.
- **Smart shuffle is shown, not offered.** Spotify reports smart shuffle in the playback state, but its shuffle
  endpoint takes a plain true/false and has no way to ask for the smart kind. The deck therefore says when smart
  shuffle is on and can turn shuffle off, but only Spotify itself can turn it on.
- **The queue is only twenty deep.** Spotify's queue endpoint returns the song playing plus twenty more and
  takes no paging parameters, so that is the whole of what Queue Picker can show. Song Picker scrolls a whole
  collection instead.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install requests Pillow pytest
.venv/bin/python -m pytest tests/ -q
```

The tests run against a fake Spotify API — no account and no deck needed. `spotify/` and `rendering/` import
neither GTK nor StreamController, which is what keeps them testable; the rules the actions rely on live there
for that reason.

To verify the Spotify half inside the real Flatpak sandbox:

```bash
flatpak run --command=python3 com.core447.StreamController \
    spotify_probe.py --client-id <YOUR_CLIENT_ID>
```

Icons are generated from one geometry table, so the key art and the SVGs in StreamController's UI cannot drift
apart. After editing `rendering/icons.py`:

```bash
.venv/bin/python tools/generate_assets.py
```

## Licence

GPL-3.0-or-later. Icons and rendered key art are original work; see `attribution.json`.
