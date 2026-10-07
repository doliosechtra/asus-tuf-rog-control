"""Backend wrappers for asus-tuf-control.

All reads are unprivileged (sysfs, sensors, nvidia-smi, glxinfo,
asusctl/supergfxctl queries). Writes to asusd/supergfxd go through
their D-Bus interfaces (no sudo/pkexec — running as root breaks the
session bus and the set is silently lost). Only the raw sysfs
fan_boost_mode write needs pkexec.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass


def run(cmd: list[str], timeout: int = 8) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout: {' '.join(cmd)}"


def read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def find_hwmon_by_name(name: str) -> str | None:
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read_file(os.path.join(d, "name")) == name:
            return d
    return None


def get_model() -> str:
    """DMI product name, cleaned up. Generic across TUF/ROG models."""
    product = read_file("/sys/class/dmi/id/product_name") or ""
    product = " ".join(product.replace("_", " ").split())
    if not product or product.lower() in ("unknown", "to be filled by o.e.m."):
        vendor = read_file("/sys/class/dmi/id/sys_vendor") or "ASUS"
        return f"{vendor} laptop"
    # Collapse DMI stutter like "FX516PC FX516PC".
    words = product.split()
    deduped = [words[0]]
    for wd in words[1:]:
        if wd.lower() != deduped[-1].lower():
            deduped.append(wd)
    return " ".join(deduped)


def find_fan_hwmon() -> str | None:
    """First hwmon (asus family) that actually has fan1_input.

    Kernel <6.x: 'asus_nb_wmi'; 6.x+: 'asus' for RPM + optional
    'asus_custom_fan_curve' for curves. Never assume one name.
    """
    for name in ("asus", "asus_nb_wmi", "asus_custom_fan_curve"):
        d = find_hwmon_by_name(name)
        if d and os.path.exists(os.path.join(d, "fan1_input")):
            return d
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if os.path.exists(os.path.join(d, "fan1_input")) and \
                (read_file(os.path.join(d, "name")) or "").startswith("asus"):
            return d
    return None


# ---------- Fans / thermals ----------

@dataclass
class FanStatus:
    cpu_rpm: int | None
    gpu_rpm: int | None
    mid_rpm: int | None  # 3rd fan on some ROG (Strix/Scar); None on 2-fan TUF
    cpu_temp: float | None
    package_temp: float | None
    nvme_temp: float | None
    profile: str
    boost: int | None
    fan_curve_supported: bool


def _read_int(path: str) -> int | None:
    v = read_file(path)
    if v is None:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _read_millidegree(path: str) -> float | None:
    raw = _read_int(path)
    return round(raw / 1000.0, 1) if raw is not None else None


def get_fan_rpms() -> tuple[int | None, int | None, int | None]:
    hwmon = find_fan_hwmon()
    if hwmon:
        return (_read_int(f"{hwmon}/fan1_input"),
                _read_int(f"{hwmon}/fan2_input"),
                _read_int(f"{hwmon}/fan3_input"))
    return None, None, None


def get_cpu_temps() -> tuple[float | None, float | None]:
    ct = find_hwmon_by_name("coretemp")
    pkg = _read_millidegree(f"{ct}/temp1_input") if ct else None
    core0 = _read_millidegree(f"{ct}/temp2_input") if ct else None
    if pkg is None:  # fallback: parse sensors
        rc, out, _ = run(["sensors"])
        if rc == 0:
            m = re.search(r"Package id 0:\s+\+([\d.]+)", out)
            if m:
                pkg = float(m.group(1))
            m2 = re.search(r"Core 0:\s+\+([\d.]+)", out)
            if m2:
                core0 = float(m2.group(1))
    return core0, pkg


def get_profile() -> str:
    rc, out, _ = run(["asusctl", "profile", "get"])
    if rc != 0:
        return "unknown"
    m = re.search(r"Active profile:\s*(\w+)", out)
    return m.group(1) if m else "unknown"


def get_profile_map() -> dict:
    """Active + per-source mapping, so the GUI follows asusd autoswitch
    instead of fighting it. e.g. {'active': 'Balanced',
    'ac': 'Balanced', 'battery': 'Quiet'}."""
    info = {"active": "unknown", "ac": "?", "battery": "?"}
    rc, out, _ = run(["asusctl", "profile", "get"])
    if rc != 0:
        return info
    for key, pat in (("active", r"Active profile:\s*(\w+)"),
                     ("ac", r"AC profile\s*(\w+)"),
                     ("battery", r"Battery profile\s*(\w+)")):
        m = re.search(pat, out)
        if m:
            info[key] = m.group(1)
    return info


def on_ac_power() -> bool | None:
    """True = AC, False = battery, None = unknown."""
    for psu in ("AC0", "ACAD", "ADP0", "AC"):
        v = read_file(f"/sys/class/power_supply/{psu}/online")
        if v is not None:
            try:
                return int(v) == 1
            except ValueError:
                continue
    return None


def set_profile_source_cmd(profile: str, source: str) -> list[str]:
    """Persist mapping: source 'ac' -> -a, 'battery' -> -b."""
    flag = "-a" if source == "ac" else "-b"
    return ["asusctl", "profile", "set", flag, profile]


def get_boost() -> int | None:
    return _read_int("/sys/devices/platform/asus-nb-wmi/fan_boost_mode")


def fan_curve_supported() -> bool:
    rc, _, _ = run(["asusctl", "fan-curve", "--get-enabled"])
    return rc == 0


def get_fan_status() -> FanStatus:
    cpu_rpm, gpu_rpm, mid_rpm = get_fan_rpms()
    core0, pkg = get_cpu_temps()
    nvme = None
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read_file(os.path.join(d, "name")) == "nvme":
            t = _read_millidegree(os.path.join(d, "temp1_input"))
            if t is not None and (nvme is None or t > nvme):
                nvme = t
            break
    return FanStatus(
        cpu_rpm=cpu_rpm,
        gpu_rpm=gpu_rpm,
        mid_rpm=mid_rpm,
        cpu_temp=core0,
        package_temp=pkg,
        nvme_temp=nvme,
        profile=get_profile(),
        boost=get_boost(),
        fan_curve_supported=fan_curve_supported(),
    )


def set_profile_cmd(profile: str) -> list[str]:
    # asusd handles privilege via D-Bus — do NOT wrap in pkexec/sudo
    # (running as root breaks the user session bus and the set is lost).
    return ["asusctl", "profile", "set", profile]


# ---------- Performance profile: KDE side (power-profiles-daemon) ----------
#
# asusd owns the ASUS profile (Quiet/Balanced/Performance); KDE's battery
# widget + powerdevil follow power-profiles-daemon (power-saver/balanced/
# performance). Both write platform_profile-ish knobs, so set both on one
# click — same "two sides of the coin" approach as the battery limit.

ASUS_TO_PPD = {"Quiet": "power-saver", "Balanced": "balanced",
               "Performance": "performance"}
PPD_TO_ASUS = {v: k for k, v in ASUS_TO_PPD.items()}


def get_ppd_profile() -> str:
    rc, out, _ = run(["powerprofilesctl", "get"])
    return out.strip() if rc == 0 and out else "unknown"


def set_ppd_cmd(asus_profile: str) -> list[str]:
    # powerprofilesctl needs no root for the local active user (polkit).
    return ["powerprofilesctl", "set", ASUS_TO_PPD.get(asus_profile, "balanced")]


def set_boost_cmd(on: bool) -> list[str]:
    # pkexec + sh -c so the redirect runs as root
    return ["pkexec", "sh", "-c",
            f"echo {1 if on else 0} > /sys/devices/platform/asus-nb-wmi/fan_boost_mode"]


# ---------- Graphics ----------

@dataclass
class GpuStatus:
    mode: str  # Integrated | Hybrid | ...
    supported: list[str]
    power_status: str
    pend_action: str
    pend_mode: str
    default_renderer: str
    nvidia_temp: float | None
    nvidia_util: int | None
    nvidia_power: float | None
    nvidia_procs: int
    active: str  # Intel iGPU | NVIDIA dGPU | Unknown
    active_detail: str


def _supergfx(args: list[str]) -> str:
    rc, out, _ = run(["supergfxctl"] + args)
    return out if rc == 0 else ""


def get_gfx_mode() -> str:
    return _supergfx(["--get"]) or "unknown"


def get_gfx_supported() -> list[str]:
    raw = _supergfx(["--supported"])
    m = re.search(r"\[(.*)\]", raw)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def nvidia_present() -> bool:
    return shutil.which("nvidia-smi") is not None


def query_nvidia() -> tuple[float | None, int | None, float | None]:
    if not nvidia_present():
        return None, None, None
    rc, out, _ = run(["nvidia-smi",
                      "--query-gpu=temperature.gpu,utilization.gpu,power.draw",
                      "--format=csv,noheader,nounits"])
    if rc != 0 or not out:
        return None, None, None
    try:
        t, u, p = [x.strip() for x in out.split(",")]
        temp = float(t) if t not in ("N/A", "") else None
        util = int(float(u)) if u not in ("N/A", "") else None
        power = float(p) if p not in ("N/A", "") else None
        return temp, util, power
    except ValueError:
        return None, None, None


def nvidia_proc_count() -> int:
    if not nvidia_present():
        return 0
    rc, out, _ = run(["nvidia-smi",
                      "--query-compute-apps=pid",
                      "--format=csv,noheader"])
    if rc != 0 or not out or "No running" in out:
        # fall back: count lines of pmon-style listing is overkill; 0 is fine
        # when only graphics (C+G) clients exist without compute
        rc2, out2, _ = run(["nvidia-smi", "--query-gpu=memory.used",
                            "--format=csv,noheader,nounits"])
        try:
            return 1 if rc2 == 0 and float(out2.split()[0]) > 50 else 0
        except (ValueError, IndexError):
            return 0
    return len([l for l in out.splitlines() if l.strip().isdigit()])


def default_renderer() -> str:
    rc, out, _ = run(["glxinfo", "-B"])
    if rc != 0:
        return "unknown"
    m = re.search(r"OpenGL renderer string:\s*(.+)", out)
    return m.group(1).strip() if m else "unknown"


def get_gpu_status() -> GpuStatus:
    mode = get_gfx_mode()
    supported = get_gfx_supported()
    status = _supergfx(["--status"])
    pend_action = _supergfx(["--pend-action"])
    pend_mode = _supergfx(["--pend-mode"])
    renderer = default_renderer()
    ntemp, nutil, npower = query_nvidia()
    nprocs = nvidia_proc_count()

    # Active-GPU heuristic (no-MUX models: Hybrid = PRIME offload, iGPU
    # drives the desktop; MUX models: dGPU may drive it directly):
    # NVIDIA counts as active when it has real load or compute clients;
    # otherwise the Intel iGPU is driving the desktop.
    if "nvidia" in renderer.lower():
        active, detail = "NVIDIA dGPU", "Default GL renderer is NVIDIA"
    elif nutil is not None and nutil > 5:
        active = "NVIDIA dGPU"
        detail = f"dGPU offload active (util {nutil}%, procs {nprocs})"
    elif mode == "Integrated":
        active, detail = "Intel iGPU", "dGPU force-disabled (Integrated mode)"
    elif nprocs > 0:
        active = "Intel iGPU"
        detail = f"Desktop on iGPU, {nprocs} dGPU offload client(s) idle (util {nutil}%)"
    elif ntemp is None:
        active, detail = "Intel iGPU", "dGPU powered down / unreachable"
    else:
        active = "Intel iGPU"
        detail = f"Desktop on iGPU, dGPU idle ({ntemp}C, {npower}W)" if ntemp else "Desktop on iGPU, dGPU idle"
    return GpuStatus(mode, supported, status, pend_action, pend_mode,
                     renderer, ntemp, nutil, npower, nprocs, active, detail)


def set_gfx_cmd(mode: str) -> list[str]:
    # Any mode the model supports (Integrated/Hybrid/AsusMuxDgpu/...).
    # supergfxd handles privilege via D-Bus/polkit — no pkexec needed.
    return ["supergfxctl", "--mode", mode]


def prime_run_cmd(app: str) -> list[str]:
    return ["prime-run"] + app.split()


# ---------- Battery ----------

@dataclass
class BatteryStatus:
    percent: int | None
    state: str
    limit_now: int | None       # kernel sysfs (asusctl-managed, the real one)
    limit_asusctl: str          # asusctl battery info text
    upower_start: str           # UPower/KDE-visible thresholds (from udev hwdb)
    upower_end: str
    hwdb_start: str             # /etc/.../61-battery-local.hwdb override or shipped default
    hwdb_end: str
    hwdb_source: str
    rate_w: float | None
    health_pct: float | None


HWDB_LOCAL = "/etc/udev/hwdb.d/61-battery-local.hwdb"
HWDB_SHIPPED = "/usr/lib/udev/hwdb.d/60-upower-battery.hwdb"


def get_hwdb_limit() -> tuple[str, str, str]:
    """Returns (start, end, source). Local override wins over shipped default.

    UPower/KDE read charge thresholds from the udev hwdb (CHARGE_LIMIT),
    while asusd writes sysfs directly — that's the 'two sides of the coin'.
    """
    for path, tag in ((HWDB_LOCAL, "local override"),
                      (HWDB_SHIPPED, "shipped default")):
        txt = read_file(path)
        if txt:
            m = re.search(r"^\s*CHARGE_LIMIT=(\S+)", txt, re.M)
            if m:
                parts = m.group(1).split(",")
                if len(parts) == 2:
                    return parts[0], parts[1], f"{tag} ({path})"
    return "n/a", "n/a", "not found"


def find_battery_psu() -> str:
    """BAT0/BAT1/BATC/BATT — different ASUS models expose different names."""
    for name in ("BAT0", "BAT1", "BATC", "BATT"):
        if os.path.isdir(f"/sys/class/power_supply/{name}"):
            return name
    cands = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    return os.path.basename(cands[0]) if cands else "BAT0"


def find_upower_battery() -> str:
    """First UPower battery device path (not line_power or display)."""
    rc, out, _ = run(["upower", "-e"])
    if rc == 0:
        for line in out.splitlines():
            if "battery_BAT" in line:
                return line.strip()
    return "/org/freedesktop/UPower/devices/battery_BAT0"


def get_battery() -> BatteryStatus:
    base = f"/sys/class/power_supply/{find_battery_psu()}"
    pct = _read_int(f"{base}/capacity")
    state = read_file(f"{base}/status") or "Unknown"
    limit = _read_int(f"{base}/charge_control_end_threshold")
    rc, out, _ = run(["asusctl", "battery", "info"])
    asus_line = out.splitlines()[0] if rc == 0 and out else "n/a"
    # UPower thresholds (KDE-visible, may differ from sysfs — see README)
    ustart = uend = "n/a"
    rc2, out2, _ = run(["upower", "-i", find_upower_battery()])
    if rc2 == 0:
        m1 = re.search(r"charge-start-threshold:\s*(\S+)", out2)
        m2 = re.search(r"charge-end-threshold:\s*(\S+)", out2)
        if m1:
            ustart = m1.group(1)
        if m2:
            uend = m2.group(1)
    rate = None
    pw = read_file(f"{base}/power_now")
    if pw is not None:
        try:
            rate = round(int(pw) / 1_000_000.0, 1)
        except ValueError:
            rate = None
    health = None
    # energy_* (µWh) on most models, charge_* (µAh) on others — same ratio.
    fe, fd = read_file(f"{base}/energy_full"), read_file(f"{base}/energy_full_design")
    if fe is None or fd is None:
        fe, fd = read_file(f"{base}/charge_full"), read_file(f"{base}/charge_full_design")
    if fe and fd:
        try:
            health = round(int(fe) / int(fd) * 100.0, 1)
        except (ValueError, ZeroDivisionError):
            health = None
    hstart, hend, hsrc = get_hwdb_limit()
    return BatteryStatus(pct, state, limit, asus_line, ustart, uend,
                         hstart, hend, hsrc, rate, health)


# ---------- Fan curves (8-point, kernel asus_custom_fan_curve hwmon) ----------
#
# Same interface g-helper-linux uses: per fan, 8x (temp °C, duty 0-255) in
# pwm{1,2,3}_auto_point{1-8}_{temp,pwm}, then pwmN_enable=1 pushes the curve
# to the EC via WMI DEVS (kernel-internal). =2 disables custom curve
# (back to firmware defaults), =3 resets to BIOS factory defaults.
# The kernel only creates this hwmon if the firmware answers the fan-curve
# WMI probes (fan_curve_check_present in asus-wmi.c). Many TUF/Dash
# firmwares don't answer them — so this stays dormant there until
# a BIOS/kernel change exposes it. Detection is dynamic, no hardcoding.

CURVE_POINTS = 8
CURVE_HWMON_NAME = "asus_custom_fan_curve"


def find_curve_hwmon(search_root: str = "/sys/class/hwmon") -> str | None:
    """Return hwmon dir with pwm1_auto_point1_temp, or None if unsupported."""
    import glob as _glob
    for d in sorted(_glob.glob(f"{search_root}/hwmon*")):
        if read_file(os.path.join(d, "name")) == CURVE_HWMON_NAME:
            probe = os.path.join(d, "pwm1_auto_point1_temp")
            if os.path.exists(probe):
                return d
    # Legacy kernels (<6.x) exposed curve files on the plain "asus" hwmon.
    asus = find_hwmon_by_name("asus")
    if asus and os.path.exists(os.path.join(asus, "pwm1_auto_point1_temp")):
        return asus
    return None


def curve_fan_count(hwmon: str) -> int:
    """2 or 3 depending on pwm3_enable presence (readable, not phantom)."""
    p3 = os.path.join(hwmon, "pwm3_enable")
    if os.path.exists(p3) and _read_int(p3) is not None and _read_int(p3) >= 0:
        return 3
    return 2


ENABLE_LABELS = {0: "off", 1: "custom curve active", 2: "firmware default"}


def read_curve(hwmon: str, fan: int) -> tuple[list[int], list[int], int | None] | None:
    """Read 8 temps (°C) + 8 duties (%) + pwm_enable state. None on error."""
    pwm = fan + 1
    temps, duties = [], []
    for i in range(1, CURVE_POINTS + 1):
        t = _read_int(f"{hwmon}/pwm{pwm}_auto_point{i}_temp")
        p = _read_int(f"{hwmon}/pwm{pwm}_auto_point{i}_pwm")
        if t is None or p is None or t < 0 or p < 0:
            return None
        temps.append(t)
        duties.append(round(p * 100 / 255))
    return temps, duties, _read_int(f"{hwmon}/pwm{pwm}_enable")


def validate_curve(temps: list[int], duties: list[int]) -> str | None:
    """Return error string, or None if valid."""
    if len(temps) != CURVE_POINTS or len(duties) != CURVE_POINTS:
        return f"need exactly {CURVE_POINTS} points"
    if any(t < 0 or t > 120 for t in temps):
        return "temps must be 0–120°C"
    if any(d < 0 or d > 100 for d in duties):
        return "duties must be 0–100%"
    if any(b < a for a, b in zip(temps, temps[1:])):
        return "temps must be non-decreasing"
    return None


def _curve_write_script(hwmon: str, fan: int, temps: list[int], duties: list[int]) -> str:
    pwm = fan + 1
    parts = []
    for i, (t, d) in enumerate(zip(temps, duties), 1):
        parts.append(f"echo {int(t)} > {hwmon}/pwm{pwm}_auto_point{i}_temp")
        parts.append(f"echo {int(round(d * 255 / 100))} > {hwmon}/pwm{pwm}_auto_point{i}_pwm")
    parts.append(f"echo 1 > {hwmon}/pwm{pwm}_enable")
    return " && ".join(parts)


def write_curve_cmd(hwmon: str, fan: int, temps: list[int], duties: list[int]) -> list[str]:
    """Write 16 points first, then pwm_enable=1 (pushes to EC). Needs root."""
    err = validate_curve(temps, duties)
    if err:
        raise ValueError(err)
    return ["pkexec", "sh", "-c", _curve_write_script(hwmon, fan, temps, duties)]


def curve_enable_cmd(hwmon: str, fan: int, mode: int) -> list[str]:
    """mode: 1 = enable custom, 2 = firmware defaults, 3 = BIOS factory reset."""
    if mode not in (1, 2, 3):
        raise ValueError("mode must be 1, 2 or 3")
    pwm = fan + 1
    return ["pkexec", "sh", "-c", f"echo {mode} > {hwmon}/pwm{pwm}_enable"]


def set_limit_cmd(limit: int) -> list[str]:
    limit = max(20, min(100, int(limit)))
    # asusd handles privilege via D-Bus — do NOT wrap in pkexec/sudo.
    return ["asusctl", "battery", "limit", str(limit)]


def hwdb_set_cmd(limit: int) -> list[str]:
    """Align the UPower/KDE side: write local hwdb override so UPower
    reports/enforces the same end limit. Needs root (one pkexec prompt)."""
    limit = max(20, min(100, int(limit)))
    start = max(0, limit - 5)  # keep KDE's start=end-5 convention (was 75/80)
    script = (
        "mkdir -p /etc/udev/hwdb.d && "
        f"printf '%s\\n' 'battery:*:*:dmi:*' ' CHARGE_LIMIT={start},{limit}'"
        f" > {HWDB_LOCAL} && "
        "systemd-hwdb update && "
        "udevadm trigger --subsystem-match=power_supply && "
        "systemctl restart upower"
    )
    return ["pkexec", "sh", "-c", script]
