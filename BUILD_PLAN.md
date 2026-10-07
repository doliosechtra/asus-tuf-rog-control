# Build Plan — asus-tuf-control (FX516PC)

> Host: `FX516PC` — ASUS TUF Dash F15 FX516PC / i5-11300H / Iris Xe + RTX 3050 Mobile
> OS: CachyOS rolling, kernel `7.2.8-1-cachyos`, KDE Plasma Wayland
> Date audited: 2026-10-07

## 1. Objective

Give the user simple, safe control over:

1. **Fan behavior** — monitor RPM/temps, switch cooling modes (profiles + boost).
2. **Graphics switching** — switch iGPU / Hybrid, launch apps on dGPU, optionally disable dGPU.

No kernel patching / EC flashing in v1. Use only supported `asusctl` / `supergfxctl` / `hwmon` interfaces.

## 2. System audit (what was found)

### 2.1 Hardware / OS

- `ASUSTeK FX516PC_FX516PC 1.0`, CPU `11th Gen i5-11300H (4C/8T, up to 4.4GHz)`, 15GiB RAM + 15GiB swap.
- GPUs:
  - `00:02.0 Intel TigerLake-LP GT2 Iris Xe` — driver `i915`, default renderer (`glxinfo` = Intel Mesa 26.2.4).
  - `01:00.0 NVIDIA GA107M RTX 3050 Mobile 4GB` — driver `nvidia 615.71.09` (open), `nvidia_drm/modeset/uvm` loaded.
- No MUX / Advanced Optimus. `supergfxctl --supported` = `[Integrated, Hybrid]` only.

### 2.2 Graphics stack — ALREADY INSTALLED & WORKING

| Component | Version / state |
|---|---|
| `supergfxd.service` | `active`, mode `Hybrid` |
| `supergfxctl` | `5.2.7-2`, `/usr/bin/supergfxctl` |
| `asusd.service` | `active` |
| `asusctl` | `6.5.0-1` |
| `rog-control-center` | `6.5.0-1`, `.desktop`: `org.opengamingcollective.rog-control-center.desktop` |
| `nvidia-prime` + `prime-run` | `1.0-6`, verified: `prime-run glxinfo` → `NVIDIA RTX 3050` |
| `nvidia-utils/settings` | `615.71.09`, `nvidia-smi` OK (57°C, P8, 6W/60W idle) |
| `nvidia-powerd.service` | `active`, logs `Dynamic Boost DC controller disabled by SBIOS` (expected on this model) |
| Vulkan ICDs | `intel_hasvk/intel/nvidia` all present |
| `power-profiles-daemon` | `0.30-3`, `active` — runs alongside `asusd`, potential conflict |
| NOT installed / not needed | `prime-select`, `optimus-manager`, `envycontrol`, `bumblebee` |

`xrandr --listproviders` = 0 providers (expected under Wayland/Xwayland, not a bug).

### 2.3 Fan / thermal stack — PARTIALLY SUPPORTED

| Component | State |
|---|---|
| `lm_sensors` | `1:3.6.2-1.1`, `sensors` works: `asus-isa-000a cpu_fan 3200 RPM, gpu_fan 3700 RPM`, `coretemp ~59°C`, `acpitz 58°C`, `BAT0 50.7W charging` |
| `fancontrol` / `pwmconfig` | binaries present (`/usr/bin/fancontrol`, `/usr/bin/pwmconfig`) but **not usable** on this EC |
| `hwmon5 (asus)` | `fan1_input/fan2_input` readable, `pwm1_enable=2 (auto)`, `pwm2_enable=0`, **no `pwm1/pwm2` value files**, `sensors` shows `pwm1/pwm2: N/A` |
| `fan_boost_mode` | `/sys/devices/platform/asus-nb-wmi/fan_boost_mode = 0` (off) |
| `asusctl profile` | works: `Quiet/Balanced/Performance`, current `Balanced (AC) / Quiet (Battery)` |
| `asusctl armoury` | works: `charge_mode, dgpu_disable, panel_overdrive` |
| `asusctl fan-curve` | **NOT SUPPORTED on FX516PC**: `Unknown interface 'xyz.ljones.FanCurves'` for all profiles |
| NOT installed | `thinkfan` (ThinkPad-only, skip), `nbfc-linux` (not in repos check), `coolercontrol 5.0.1-1` available in `cachyos` repo (monitor-only for this EC), `liquidctl 1.16.0-1` (AIO, not relevant) |
| Battery health | `BAT0 33%, 50.57Wh/75.99Wh design = 66.5%`, `653 cycles`, limit already `75–80%` via `asusctl` |

**Conclusion:** direct RPM / PWM control is not exposed by `asus-nb-wmi` on this model. Realistic v1 fan control = `profile set + fan-boost toggle + monitoring`. Custom curves require EC reverse-engineering (out of scope, risky).

## 3. Constraints / decisions

1. Do NOT attempt `pwmconfig`/`fancontrol` writes — no `pwmX` nodes, will fail or spin down fans unsafely.
2. Do NOT install `optimus-manager`/`envycontrol` — conflicts with `supergfxd` on CachyOS.
3. `supergfxctl` only supports `Integrated` / `Hybrid` here — no `Dedicated`/`Vfio`/`Compute` modes, no MUX.
4. `power-profiles-daemon` vs `asusd` both active — pick one as source of truth (recommend `asusctl profile`), document conflict.
5. All privileged writes need `sudo` / polkit; scripts must be idempotent and log.
6. Battery is degraded (66%) — avoid sustained `Performance + Hybrid` on battery in defaults.

## 4. Architecture (v1)

```
asus-tuf-control/
  README.md
  BUILD_PLAN.md (this file)
  scripts/
    tuf-status      # one-shot: temps, fans, gpu, profile, gfx mode, battery
    tuf-fan-mode    # quiet|balanced|performance|boost-on|boost-off
    tuf-gpu-mode    # integrated|hybrid|offload <cmd> | dgpu-disable on|off
  systemd/ (optional v1.1)
    tuf-auto-profile.service  # AC/battery -> Performance/Balanced/Quiet
  kde/ (optional v1.1)
    krunner/plasmoid or .desktop shortcuts + kinfocenter notes
```

No daemon in v1 — thin wrappers around `asusctl`, `supergfxctl`, `prime-run`, `/sys/class/hwmon`, `sensors`.
Language: `bash` only (matches CachyOS base, no new deps).

## 5. Phases

### Phase 0 — Done (audit)
- [x] `lspci`, `nvidia-smi`, `prime-run glxinfo`, `sensors`, `hwmon`, `asusctl/supergfxctl` status, service states.

### Phase 1 — Read-only status (safe, no root writes)
- [ ] `scripts/tuf-status`: `sensors`, `/sys/class/hwmon/hwmon5/fan*_input`, `coretemp`, `nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu`, `asusctl profile get`, `supergfxctl --get/--status`, `asusctl battery info`, `upower`.
- Validate: output matches manual commands on AC + battery.

### Phase 2 — Fan modes (profile + boost)
- [ ] `scripts/tuf-fan-mode`: `asusctl profile set Quiet|Balanced|Performance`, `echo 0/1 | sudo tee /sys/devices/platform/asus-nb-wmi/fan_boost_mode`, verify via `cat` + `sensors` after 10s.
- [ ] Document: no custom curve on FX516PC, link `asusctl fan-curve` error as proof.
- Validate: profile switches persist after reboot (`asusd` restores), boost toggles RPM audibly.

### Phase 3 — Graphics switching
- [ ] `scripts/tuf-gpu-mode`: `sudo supergfxctl --mode Integrated|Hybrid`, print pending-action reboot notice, `prime-run <cmd>` wrapper, `asusctl armoury get dgpu_disable` / `set dgpu_disable 0|1` (with reboot warning).
- [ ] KDE notes: `rog-control-center` GUI path, Wayland `__NV_PRIME_RENDER_OFFLOAD=1` env, verify with `glxinfo` vs `prime-run glxinfo`.
- Validate: `Integrated` → `nvidia-smi` shows powered down / no processes; `Hybrid + prime-run` → NVIDIA renderer.

### Phase 4 — Polish / safety
- [ ] AC/battery auto-profile (udev or systemd, disabled by default).
- [ ] `.desktop` shortcuts for KDE launcher + `README` cheat-sheet.
- [ ] Guardrails: refuse `Performance` on battery without `--force`, never write `pwm*_enable=1` (manual) automatically, log every privileged write.
- [ ] Optional evaluate-only: `coolercontrol` for graphs (no EC control expected), `nbfc-linux` research spike — do NOT enable by default.

## 6. Validation checklist

- `tuf-status` runs as user, exit 0, no sudo.
- `tuf-fan-mode balanced` → `asusctl profile get` shows `Balanced`.
- `tuf-gpu-mode hybrid` → `supergfxctl --get` = `Hybrid`, `prime-run glxinfo | grep NVIDIA` passes.
- Reboot persistence: profile + gfx mode survive (via `asusd`/`supergfxd`).
- No `fancontrol`/`pwmconfig` dependency in scripts.

## 7. Risks

- Expectation mismatch: user wants “fan speed slider” — hardware does not expose it; must communicate profile-only control.
- `nbfc` / EC direct writes can brick cooling — explicitly out of scope for v1.
- `power-profiles-daemon` fighting `asusd` — document, suggest disabling PPD if profiles misbehave (`systemctl disable --now power-profiles-daemon` only after testing).
- Battery wear (66%, 653 cycles) — high-fan/high-perf testing should be on AC.
- `nvidia-powerd` Dynamic Boost error is BIOS-limited, not fixable in software.

## 8. Next steps after v1

- KDE tray / Plasma System Monitor sensor page.
- Logging temps/fans to CSV for curve proposal to `asusctl` upstream (FX516PC lacks `FanCurves` support — file issue with `asusctl info` + `acpi` dump).
- Re-evaluate `supergfxctl` `Vfio/Compute` if future BIOS exposes MUX.
