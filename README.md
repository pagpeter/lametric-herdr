# lametric-herdr

A [herdr](https://herdr.dev) plugin that shows the state of your coding agents,
across all your herdr machines, on a LaMetric Time. Visual only, no sound.

The clock app's icon becomes a live grid with one 2×2 block per agent, in
herdr's status colors:

| State | Block |
|---|---|
| idle | green, one pixel missing (herdr's ○) |
| working | yellow, pulsing |
| done (not yet viewed) | teal |
| blocked (needs input) | red, pulsing |

Up to 9 agents fit with gaps, 16 without. Local agents come first, then each
saved machine in `herdr machine list` order. When an agent becomes blocked or
done, a silent notification scrolls its title once.

## How it works

One background watcher (`watcher.py`) does all the work:

- **Local agents:** `agent.list` over the herdr socket every second.
- **Saved machines:** `herdr --machine <id> agent list` every 2 seconds. herdr
  owns the SSH connection and reuses it between calls, so the plugin holds no
  credentials and runs none of its own code on remote machines.
- Redraws the clock only when the grid changes.

Polling instead of events: herdr only offers per-pane status subscriptions,
and a finished agent turns from done to idle without any event when you view
it.

herdr hooks (startup, and frequent events such as pane focus) only make sure
the watcher is running, so installing the plugin is the whole setup. The
watcher exits when the plugin is disabled, unlinked, or reinstalled.

Python 3.9+ standard library only.

## Setup

1. Get the device API key: developer.lametric.com → Devices.
2. Install the plugin and configure it:

   ```bash
   herdr plugin link ~/coding/own/lametric-herdr
   cp config.example.env "$(herdr plugin config-dir lametric-herdr)/.env"
   $EDITOR "$(herdr plugin config-dir lametric-herdr)/.env"
   herdr plugin action invoke lametric-herdr.test
   ```

## Commands

| Action | What it does |
|---|---|
| `lametric-herdr.status` | Watcher, device, and per-machine state |
| `lametric-herdr.refresh` | Force a redraw and re-read machines and config |
| `lametric-herdr.test` | Check the connection and send a test alert |
| `lametric-herdr.toggle-alerts` | Turn alerts on/off; the clock keeps updating |
| `lametric-herdr.restart` | Restart the watcher |

Action output: `herdr plugin log list --plugin lametric-herdr`. Watcher log:
`watcher.log` in the plugin state directory (`~/.local/state/herdr/plugins/lametric-herdr`).

## LaMetric Time v1 notes

- The display is "mixed": the left 8×8 is RGB, the remaining 29×8 is white-only.
  A wide GIF icon shows color in the 8×8 and drops every non-gray pixel elsewhere.
- Channel values below ~90 do not light up, so dim animation steps stay above that.
- The clock app's `clock.clockface` action accepts a custom (animated) icon, so
  the agent grid sits next to the time without changing apps.
