# De Dietrich Smart for Home Assistant

A Home Assistant integration for De Dietrich heating systems controlled by the **Smart TC** Wi-Fi room thermostat. It uses the account you already have in the **De Dietrich Smart** app.

> [!WARNING]
> **This was built for my own home. It may not work for yours.**
>
> - I wrote it to integrate *my* De Dietrich heat pump and *my* Smart TC thermostat into *my* Home Assistant, and it fits that setup.
> - I'm open-sourcing it in case it helps someone with a similar system. It comes with **no guarantees and no support commitment**.
> - Other heat pumps, boilers, hybrid systems, multiple zones or cooling setups may behave differently, or not work at all.
> - It talks to an **unofficial, undocumented cloud API** that can change or break at any time.
> - It is **not affiliated with, endorsed by or supported by De Dietrich or BDR Thermea**.
> - It sends real commands to your heating. Use it at your own risk.

## How it works

The Smart TC has no local API. On my network, its Wi-Fi module has no open ports: it only keeps an outbound connection to BDR Thermea's cloud.

This integration therefore talks to the same cloud API as the De Dietrich Smart app:
- It signs in with your app email and password.
- It polls your system once a minute. If the cloud doesn't answer, it keeps showing the last values for up to 5 minutes before the entities become unavailable.
- It sends your commands the way the app does.

BDR Thermea runs one backend for several brands, so Remeha Home, Baxi and Brötje accounts may work too. That's untested.

If you want fully local control, the realistic route is hardware: read and write the heat pump controller over Modbus with an ESP32 running ESPHome. See [this community thread](https://community.home-assistant.io/t/baxi-remeha-dietrich-to-esp32-with-esphome/777548).

## The setup it runs on

This is the one system the integration is known to work with: mine, in daily use since November 2026. Here's what the cloud reports for it:

| Part | Reported as |
|---|---|
| Heat pump (indoor unit) | `IDU2WH`, air-source, heating and cooling capable |
| Smart TC thermostat | gateway `RU2`, firmware 2.28.6 |
| Heating circuits | three: one with the Smart TC in it, two without a thermostat (on the heating curve only) |
| Hot water | storage tank, with boost |

If yours differs, it may still work. Please [open an issue](https://github.com/rouralberto/ha-dedietrich-smart/issues) with your diagnostics either way.

## Requirements

- **Home Assistant 2026.10 or newer.** It also supports the climate and water-heater changes in 2026.11.
- Your system already set up and working in the **De Dietrich Smart** app. If the app can't control it, this integration can't either.

## Installation

### With HACS (recommended)

1. In Home Assistant open **HACS**, then **⋮ → Custom repositories**.
2. Add `https://github.com/rouralberto/ha-dedietrich-smart` with type **Integration**.
3. Search for **De Dietrich Smart**, click **Download**, then **restart Home Assistant**.

### Manually

1. Copy the `custom_components/dedietrich` folder of this repository into your Home Assistant config folder, so that you end up with `<config>/custom_components/dedietrich/manifest.json`.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and pick **De Dietrich Smart**.
2. Enter the email and password you use in the De Dietrich Smart app.

**Where your password goes:**
- The password is stored in Home Assistant's config entry storage (`.storage/core.config_entries`), the same place other integrations keep their credentials.
- It's kept so the integration can sign in again when the cloud session expires.
- If the password stops working (for example, you changed it in the app), Home Assistant shows a notification asking you to sign in again.

## What you get

The integration creates one device for the heat pump (the "appliance"), one for each thermostat zone, and one for hot water.

| Device | Entities |
|---|---|
| **Thermostat** (each climate zone) | Climate entity, room temperature, current schedule setpoint, next setpoint and when it starts, heat demand, fireplace mode (if supported), **heating curve** slope and base temperature, maximum flow temperature |
| **Hot water** | Water heater entity, water temperature, heating on/off, comfort temperature and eco temperature |
| **Heat pump** | **Heating/cooling mode** (if your system can cool), outdoor temperature (from the appliance and from the weather service), water pressure and a pressure-problem sensor (if reported), operating mode, thermal mode, status, cloud connection, holiday mode, energy consumed/delivered today for heating and hot water, **COP today**, **seasonal efficiency (SCOP)** |

A **heating circuit without its own thermostat** still gets a climate entity, so you can set its mode and schedule. It shows up as a *Heating circuit* device, without the room temperature sensor or fireplace mode.

Some entities only appear when your system reports them:
- The heating/cooling mode and cooling energy sensors appear only if your system reports cooling.
- The heating curve appears only on zones where the cloud has one.
- Water pressure appears only if your system reports it.
- SCOP appears only if a heat pump is listed among the energy producers.

### Thermostat modes

| Home Assistant | What it does |
|---|---|
| **Auto** | Follows the active clock program. Changing the temperature overrides the program **until its next switch time**. |
| **Heat** (**Cool** while the heat pump is in *Cooling only*) | Holds the temperature you set until you change mode. |
| **Off** | Frost protection only. |
| Presets **Schedule 1–3** | Switches to one of the app's three clock programs, in Auto mode. |

### Heating/cooling mode

This applies to the whole heat pump, so every zone follows it.

| Option | What it does |
|---|---|
| **Automatic** | Heats or cools as the season requires. This is the usual setting. |
| **Heating only** | Never cools. |
| **Cooling only** | Cools. The climate entities then offer **Cool** instead of **Heat**. |

The cloud also has an *Off* mode that switches off the whole heat pump. I left it out on purpose: you can turn each zone off from its climate entity.

### Heating curve

On a heat pump the heating curve matters a lot: it sets how warm the water sent to a circuit is, depending on the outdoor temperature.
- **Slope:** how much warmer the water gets as it gets colder outside.
- **Base temperature:** the flow temperature the curve starts from.

A lower curve means a better COP, but a steeper one is needed if the house can't keep up when it's cold. Each circuit has its own curve, including circuits without a thermostat. Both are configuration entities, so they appear under the device's *Configuration* section.

> [!CAUTION]
> These change your installer's settings. Write down the current values before you touch them, and change them in small steps.

### Hot water modes

| Mode | What it does |
|---|---|
| **Schedule** | Follows the hot water clock program |
| **Comfort** | Always keeps the comfort temperature |
| **Eco** | Always keeps the eco (reduced) temperature |
| **Boost** | Heats to the comfort temperature once, right now (if your system supports it) |

Setting the water heater's temperature changes the eco temperature while in Eco mode, and the comfort temperature otherwise.

### Things to know

- **Slow updates:** the cloud applies a command a few seconds after accepting it. Home Assistant shows the new state immediately, then checks again 8 seconds later. If the cloud didn't apply the command, the state goes back.
- **Energy figures** come from the cloud's daily totals. They refresh every 15 minutes, lag behind reality by up to an hour, and reset at midnight. **COP today** is heat delivered ÷ electricity consumed, using those totals.
- **What has been tested live on the reference system:**
  - Switching between *Automatic* and *Heating only*.
  - Writing the heating curve: the API accepted a write of its current values.
  - *Cooling only* has **not** been tried yet, because these features were added in November. It uses the value that the API accepts and that other heat pump users use.
- **Not supported (yet):**
  - editing clock programs
  - holiday mode (shown, but not settable)

## Troubleshooting

**Download diagnostics:** go to Settings → Devices & services → De Dietrich Smart → ⋮ → **Download diagnostics**.
- You get the raw data the cloud sends for your system, with your email, password, tokens and serial numbers removed.
- It's the most useful thing to attach to an issue, especially if your system differs from mine.

**Debug logging:** add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.dedietrich: debug
```

**Common problems:**
- **"Wrong email or password"**: check that you can sign in to the De Dietrich Smart app with the same details.
- **Entities unavailable**: either the heat pump is offline in the cloud, or the cloud hasn't answered for more than 5 minutes. Check the *Cloud connection* sensor, the app, and the Home Assistant log.

Issues and pull requests are welcome. Remember the disclaimer, though: I can only test against my own system.

## Development

The tests run the integration inside a real Home Assistant (via [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)) against a simulated cloud:

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r tests/requirements.txt
.venv/bin/pytest
```

The code is laid out like this:

| File | What it does |
|---|---|
| `api.py` | The cloud client: sign-in and token refresh, and one method per endpoint |
| `coordinator.py` | Polling, plus applying commands and showing their effect |
| Platform files | One file per entity type |

## Credits

The cloud API was reverse engineered by [msvisser/remeha_home](https://github.com/msvisser/remeha_home) and the community around its forks. This is an independent implementation that builds on their findings. Thank you!

## License

[MIT](LICENSE). "De Dietrich" is a trademark of its owner. It's used here only to say which systems this integration is for.
