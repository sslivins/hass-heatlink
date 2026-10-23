# HeatLink for Home Assistant

Native Home Assistant integration for [**HeatLink**](https://github.com/sslivins/heatlink),
ESP32 heads that control Mitsubishi mini-split indoor units over CN105. It talks to each
head's local HTTP API (no cloud, no MQTT broker) through
[`pyheatlink`](https://pypi.org/project/pyheatlink/), and adds the HeatLink-only features:
Off-at-setpoint, Tandem heat/cool turn-taking, the outdoor unit as its own device, the head's
activity log and firmware updates.

Requires HeatLink firmware **1.5.0 or later** and Home Assistant **2026.5** or later.

## Install

**HACS:** add `sslivins/hass-heatlink` as a custom integration repository, install
**HeatLink**, and restart Home Assistant.

**Manual:** copy `custom_components/heatlink/` into your configuration's
`custom_components/` folder and restart.

## Adding heads

Heads announce themselves over zeroconf (`_mmhvac._tcp`), so they appear under
**Settings → Devices & services → Discovered**. You can also add one by IP address.

Each head needs an integration token. On the head's web page open **Settings → API Access**,
create a token and paste it into Home Assistant:

| Scope | What Home Assistant can do |
|-------|----------------------------|
| **Full admin** (recommended) | Everything: climate control, Off-at-setpoint and Tandem settings, firmware updates, restart, diagnostic resets and the activity log |
| **Climate control** | Climate control, sensors and the remote room temperature only |

Once one head on an outdoor unit is added with a **Full admin** token, the other heads on
the same outdoor unit are added with one click: Home Assistant asks the configured head to
mint a token for its neighbour, so you don't need to visit every head's web page.

If a head's token is revoked Home Assistant asks you to re-authenticate. If a head moves to
a new IP address, discovery updates it automatically, or use **Reconfigure**.

## What you get

**Per head**
- A climate entity: modes, target temperature, fan speed, vane and (where the unit has one)
  wide vane. Heat/Cool is the unit's own Auto when Tandem is off; while Tandem is on it's
  HeatLink's Auto, with a heat-to/cool-to range (needs firmware with Auto under Tandem).
- The room temperature (from the head's BLE room sensor when it has one), unit temperature,
  compressor frequency, fault code, Off-at-setpoint state and, while the head waits for its
  Tandem turn, when that turn comes up.
- Binary sensors: parked at setpoint, waiting for Tandem, defrosting, fault, unit connected.
- Off-at-setpoint restart threshold (the only per-head Off-at-setpoint setting).
- Firmware update entity with install and progress, plus restart and diagnostic-counter
  reset buttons (disabled by default).
- Diagnostics: Wi-Fi signal and drops, reset reason, brownouts, boot count, minimum supply
  voltage, free memory and last boot.

**Per outdoor unit** (heads on one outdoor unit share these)
- Tandem on/off, its state (heating, cooling, switching, idle…), when the current turn ends,
  and its minimum turn, maximum turn and changeover pause.
- Off-at-setpoint on/off, minimum off time and air checks (on/off, interval, duration).
- A mode-conflict problem sensor.

The heads hold these settings in sync with each other, so changing one here changes it on
every head.

Units don't report power or energy, so there are no power sensors.

## Remote room temperature

Under the integration's **Configure** options you can choose any Home Assistant temperature
sensor as a head's room temperature. Home Assistant keeps feeding it to the head; if the
sensor goes unavailable or Home Assistant stops, the head falls back to its own reading
within ten minutes. A head that has its own BLE room sensor keeps using that, and Home
Assistant raises a repair to say so.

The `heatlink.set_remote_temperature` action sends a one-off reading (or clears it when no
temperature is given), for automations that compute their own value.

## Events and the activity log

Everything in the head's own activity log (mode or setpoint changes from the web page or
the remote, group and Tandem changes, firmware updates…) is fired as a `heatlink_event`
event and shown in Home Assistant's Activity. Fault changes fire `heatlink_fault`.

## Dashboard cards

The integration serves two dashboard extras; no separate install is needed.

**Tandem status on a thermostat tile.** Add the feature to a Tile card for a HeatLink
climate entity:

```yaml
type: tile
entity: climate.bedroom_1
features:
  - type: custom:heatlink-tandem
```

It shows what the head is doing (for example "Paused while other rooms heat · cooling in
12 min"), or the outdoor unit's Tandem state.

**House overview.** Every head grouped by outdoor unit, with each outdoor unit's Tandem
state. Tap a head to open its controls.

```yaml
type: custom:heatlink-house-card
title: Heating
```

## Moving from MQTT

HeatLink's MQTT discovery and this integration would create duplicate entities. Once the
integration is set up, open each head's web page, go to **Settings → MQTT / Home Assistant**,
turn off **Home Assistant discovery** and **Save & reboot**. Home Assistant shows a repair
for each head that still has it on. Then update dashboards and automations to the new
entities.

## Development

```bash
pip install -e ".[tests]"
ruff check custom_components/heatlink tests
mypy custom_components/heatlink
pytest --cov=custom_components/heatlink
```
