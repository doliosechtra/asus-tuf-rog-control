# ASUS TUF/ROG Control

Fan, graphics and battery control for **ASUS TUF and ROG laptops on Linux** — one window that keeps the firmware side (`asusd`/`supergfxd`) and the desktop side (KDE/UPower/power-profiles-daemon) saying the same thing.

Python + PySide6, no new daemons: thin, auditable wrappers around tools you already trust.

## Features

- **Fans tab** — live CPU/GPU/(Mid) RPM + temps, performance profile `Quiet/Balanced/Performance`, fan-boost toggle. Applying a profile sets three things at once: the active profile, the current power source's autoswitch mapping (`-a`/`-b`, so `asusd` remembers instead of reverting), and the matching KDE power mode. The tab shows the autoswitch map and follows plug-event changes instead of fighting them.
- **Fan Curve tab** — 8-point per-fan editor (apply / back-to-firmware / factory reset) wired to the kernel `asus_custom_fan_curve` hwmon — the same interface G-Helper Linux uses. Auto-detects support: on models whose firmware doesn't answer the fan-curve WMI probes (e.g. TUF Dash F15 FX516PC) the tab explains why and disables itself.
- **Graphics tab** — GPU mode switcher populated from your model's actual `supergfxctl --supported` modes (Integrated/Hybrid on no-MUX models, plus MUX/VFIO/Compute where available), dGPU temp/util/power, `prime-run` launcher, `nvidia-settings` shortcut. Header **GPU indicator**: green = iGPU driving the desktop, red = dGPU active.
- **Battery tab** — charge bar, health, and the three faces of the charge limit side by side: firmware (sysfs/`asusd`, the real one), udev-hwdb (what UPower/KDE reports), UPower-live — with a differ-warning. **Set limit writes both sides** so they stop disagreeing.

## Requirements

- ASUS TUF/ROG laptop with the [asus-linux](https://asus-linux.org) stack: `asusctl` (`asusd` running), `supergfxctl` (`supergfxd` running for graphics switching)
- `lm_sensors` (`sensors`), `upower`, `power-profiles-daemon` (for the KDE-side integrations), `mesa-utils` (`glxinfo`)
- Python 3 + PySide6 (`requirements.txt`)
- Developed/tested on CachyOS (Arch) + KDE Plasma Wayland; should work anywhere the asus-linux stack runs

## Install

```bash
git clone https://github.com/rezaldyns/asus-tuf-rog-control.git
cd asus-tuf-rog-control
./install.sh        # user-local: ~/.local/bin + KDE launcher, no root
asus-tuf-rog-control
```

Or run without installing: `cd app && ./run.sh`.

## How it works (the dual-write design)

Most ASUS-on-Linux confusion comes from **two writers per setting**:

| Setting | Firmware side (real) | Desktop side (displayed) | This app |
|---|---|---|---|
| Charge limit | `asusctl battery limit` → sysfs | udev hwdb `CHARGE_LIMIT` → UPower/KDE | sets both |
| Perf profile | `asusctl profile set` (+ `-a`/`-b` autoswitch map) | `powerprofilesctl set` (KDE power mode) | sets both (+map) |

Writes to `asusd`/`supergfxd`/`powerprofilesctl` go over D-Bus as your user (no password). Only raw sysfs writes (fan boost, fan curves, hwdb rebuild) prompt via `pkexec`.

## Support matrix

| | Status |
|---|---|
| Tested on | ASUS TUF Dash F15 FX516PC (i5-11300H, Iris Xe + RTX 3050, CachyOS) |
| Expected to work | Any TUF/ROG with `asusd` + `supergfxd` (hardware detection is dynamic: hwmon chain, BAT0/BAT1, fan count, supported gfx modes) |
| Fan curves | Only where firmware exposes them (kernel `asus_custom_fan_curve`); otherwise profiles + boost |
| EC-backdoor manual control | Deliberately not included (untested EC families, needs root C helper) — see `BUILD_PLAN.md` |

`BUILD_PLAN.md` keeps the original development/audit notes from the FX516PC bring-up.

## Safety

- No EC flashing or raw port I/O — only vendor-supported interfaces.
- Fan-curve apply asks for confirmation; firmware reverts curves on reboot/sleep.
- Battery/profile changes are ordinary `asusctl`/UPower operations, same as their GUI tools make.

## Credits

- [asus-linux](https://asus-linux.org) (`asusctl`, `supergfxctl`, ROG Control Center) — does the hard work; this app is a unified frontend.
- Fan-curve interface research: [g-helper-linux](https://github.com/utajum/g-helper-linux) and upstream `asus-wmi.c`.

## License

MIT — see `LICENSE`.
