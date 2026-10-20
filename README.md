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

One background watcher (`src/watcher.py`) does all the work:

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

## Requirements

- herdr 0.9.0 or newer, on macOS or Linux
- `python3` (3.9+) on the herdr server's `PATH`
- A LaMetric Time on the same network as the machine running herdr

Remote machines need nothing extra: they are read through herdr's own saved
machine connections (`herdr machine list`).

## Install

### 1. Get the LaMetric API key

1. Open [developer.lametric.com](https://developer.lametric.com) and log in
   with the account your LaMetric Time is registered to.
2. Open **Devices** (top menu) and click your clock.
3. Copy the **API key**.

You also need the clock's IP address. Your router's list of connected devices
shows it.

### 2. Install the plugin

From GitHub:

```bash
herdr plugin install pagpeter/lametric-herdr
```

Or from a local checkout:

```bash
herdr plugin link /path/to/lametric-herdr
```

### 3. Configure it

```bash
cat > "$(herdr plugin config-dir lametric-herdr)/.env" <<'EOF'
LAMETRIC_HOST=your-clock-ip
LAMETRIC_API_KEY=paste-your-api-key-here
EOF
chmod 600 "$(herdr plugin config-dir lametric-herdr)/.env"
```

See [`config.example.env`](config.example.env) for the optional settings.

### 4. Check it

```bash
herdr plugin action invoke lametric-herdr.test
herdr plugin action invoke lametric-herdr.status
herdr plugin log list --plugin lametric-herdr --limit 2
```

`test` shows a "herdr connected" alert on the clock. `status` lists every
machine and its agents. The watcher starts by itself on the next herdr event,
such as switching panes; `test` also starts it right away.

## Uninstall

```bash
herdr plugin uninstall lametric-herdr   # installed from GitHub
herdr plugin unlink lametric-herdr      # linked from a local checkout
```

Within 15 seconds the watcher notices, resets the clock to its plain face, and
exits. Disabling the plugin (`herdr plugin disable lametric-herdr`) does the
same.

herdr keeps the plugin's config and state. To remove them too (paths shown are
the macOS/Linux defaults):

```bash
rm -rf ~/.config/herdr/plugins/config/lametric-herdr ~/.local/state/herdr/plugins/lametric-herdr
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
`~/.local/state/herdr/plugins/lametric-herdr/watcher.log`.

## Development

```text
herdr-plugin.toml      manifest: hooks and actions run src/lametric_herdr.py
src/lametric_herdr.py  CLI commands
src/watcher.py         background watcher, and starting/stopping it
src/herdr.py           herdr API socket and CLI access
src/lametric.py        LaMetric local API client
src/pixels.py          icons and the GIF encoder
src/config.py          paths, .env config, settings
```

```bash
python3 -m unittest discover -s tests       # tests, standard library only
python3 src/lametric_herdr.py preview       # draw local agents in the terminal
herdr plugin action invoke lametric-herdr.restart   # pick up code changes
```

## LaMetric Time v1 notes

- The display is "mixed": the left 8×8 is RGB, the remaining 29×8 is white-only.
  A wide GIF icon shows color in the 8×8 and drops every non-gray pixel elsewhere.
- Channel values below ~90 do not light up, so dim animation steps stay above that.
- The clock app's `clock.clockface` action accepts a custom (animated) icon, so
  the agent grid sits next to the time without changing apps.
