# lametric-herdr

A [herdr](https://herdr.dev) plugin that shows the state of your coding agents
on a LaMetric Time. Visual only, no sound.

- **Agent grid (always on):** the My Data DIY widget's icon is an 8×8 grid with
  one block per agent. Working agents pulse cyan, blocked agents blink amber,
  unseen finished agents are green, and idle agents are barely-lit gray. The
  text shows the most urgent count: `1 WAIT`, `2 DONE`, `3 RUN`, or `IDLE`.
- **Alerts (transient):** when an agent becomes `blocked` or `done`, a silent
  `info` notification scrolls the agent's title once, then the display returns
  to the widget.

Python 3.9+ standard library only.

## Setup

1. On the device: install **My Data DIY** from the LaMetric app store, set it to
   **Local Push**, and copy the push URL it shows.
2. Get the device API key: developer.lametric.com → Devices.
3. Link the plugin and configure it:

   ```bash
   herdr plugin link ~/coding/own/lametric-herdr
   cp config.example.env "$(herdr plugin config-dir lametric-herdr)/.env"
   $EDITOR "$(herdr plugin config-dir lametric-herdr)/.env"
   herdr plugin action invoke lametric-herdr.test
   ```

4. To keep agent state on screen permanently, turn off app auto-rotation in the
   LaMetric app and leave the DIY widget as the active app.

## Commands

| Action | What it does |
|---|---|
| `lametric-herdr.refresh` | Push the current state to the widget |
| `lametric-herdr.test` | Check the connection, send a test alert, refresh |
| `lametric-herdr.toggle-alerts` | Turn alerts on/off; the widget keeps updating |

`python3 lametric_herdr.py preview` renders the grid and alert icons in your
terminal without touching the device.

## Debugging

```bash
herdr plugin log list --plugin lametric-herdr
```

Set `DEBUG=1` in the config to log events to `debug.jsonl` in the plugin state
directory.
