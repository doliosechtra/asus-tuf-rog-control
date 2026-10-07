"""ASUS TUF/ROG Control — Fan / Graphics / Battery GUI for ASUS laptops on Linux."""
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QSlider, QProgressBar, QGroupBox,
    QFormLayout, QMessageBox, QLineEdit, QCheckBox, QStatusBar, QSpinBox,
)

import backend as B


def run_privileged(cmd: list[str], parent=None) -> bool:
    """Run a hardware-control command (D-Bus daemons auth themselves;
    only fan-boost uses pkexec), report result in status bar / dialog."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(parent, "Error", str(e))
        return False
    if p.returncode != 0:
        QMessageBox.warning(parent, "Command failed",
                            f"$ {' '.join(cmd)}\n\n{p.stderr or p.stdout or 'cancelled?'}")
        return False
    return True


class IndicatorDot(QLabel):
    COLORS = {"intel": "#22c55e", "nvidia": "#ef4444", "unknown": "#9ca3af"}

    def set_state(self, active: str):
        key = "nvidia" if "NVIDIA" in active else ("intel" if "Intel" in active else "unknown")
        c = self.COLORS[key]
        self.setStyleSheet(f"background:{c}; border-radius:9px; min-width:18px; min-height:18px; max-width:18px; max-height:18px;")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            self.model = B.get_model()
        except Exception:  # noqa: BLE001
            self.model = "ASUS laptop"
        self.setWindowTitle(f"ASUS Control — {self.model}")
        self.resize(760, 600)
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    # ----- UI construction -----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Header with GPU indicator
        head = QHBoxLayout()
        self.dot = IndicatorDot()
        self.gpu_label = QLabel("GPU: …")
        self.gpu_label.setStyleSheet("font-weight: bold; font-size: 15px;")
        self.gpu_detail = QLabel("")
        self.gpu_detail.setStyleSheet("color: gray;")
        self.gpu_detail.setWordWrap(True)
        head.addWidget(self.dot)
        head.addWidget(self.gpu_label)
        head.addWidget(self.gpu_detail, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(lambda: self.refresh(sync_inputs=True))
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._overview_tab(), "Overview")
        self.tabs.addTab(self._fans_tab(), "Fans")
        self.tabs.addTab(self._fancurve_tab(), "Fan Curve")
        self.tabs.addTab(self._graphics_tab(), "Graphics")
        self.tabs.addTab(self._battery_tab(), "Battery")
        self.setStatusBar(QStatusBar())
        # Inputs (slider, profile combo, boost check, gfx combo) are synced
        # from the system only once at startup and on explicit Refresh —
        # never by the 2s auto-timer — so the timer can't fight the user
        # while they drag/click before hitting Apply/Set.
        self._inputs_synced = False

    def _overview_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.ov_mode = QLabel("…")
        self.ov_renderer = QLabel("…")
        self.ov_nvidia = QLabel("…")
        self.ov_fans = QLabel("…")
        self.ov_temps = QLabel("…")
        self.ov_profile = QLabel("…")
        self.ov_batt = QLabel("…")
        for lab in (self.ov_mode, self.ov_renderer, self.ov_nvidia, self.ov_fans,
                    self.ov_temps, self.ov_profile, self.ov_batt):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setWordWrap(True)
        f.addRow("Graphics mode:", self.ov_mode)
        f.addRow("GL renderer:", self.ov_renderer)
        f.addRow("NVIDIA:", self.ov_nvidia)
        f.addRow("Fans:", self.ov_fans)
        f.addRow("Temps:", self.ov_temps)
        f.addRow("Profile:", self.ov_profile)
        f.addRow("Battery:", self.ov_batt)
        note = QLabel("Indicator: green = iGPU driving desktop, red = dGPU active. "
                      "No-MUX models use PRIME offload in Hybrid; MUX models can drive the display from the dGPU.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        f.addRow(note)
        return w

    def _fans_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Live readings")
        f = QFormLayout(grp)
        self.fan_cpu = QLabel("…")
        self.fan_gpu = QLabel("…")
        self.fan_mid = QLabel("…")
        self.temp_pkg = QLabel("…")
        self.temp_core = QLabel("…")
        f.addRow("CPU fan:", self.fan_cpu)
        f.addRow("GPU fan:", self.fan_gpu)
        f.addRow("Mid fan:", self.fan_mid)
        f.addRow("Package:", self.temp_pkg)
        f.addRow("Core 0:", self.temp_core)
        lay.addWidget(grp)

        grp2 = QGroupBox("Performance profile (sets ASUS + KDE together)")
        f2 = QFormLayout(grp2)
        self.profile_box = QComboBox()
        self.profile_box.addItems(["Quiet", "Balanced", "Performance"])
        self.apply_profile_btn = QPushButton("Apply profile")
        self.apply_profile_btn.clicked.connect(self.on_profile)
        h = QHBoxLayout()
        h.addWidget(self.profile_box, 1)
        h.addWidget(self.apply_profile_btn)
        hc = QWidget()
        hc.setLayout(h)
        f2.addRow("Profile:", hc)
        self.profile_map = QLabel("…")
        self.profile_map.setWordWrap(True)
        self.profile_map.setStyleSheet("color: gray;")
        f2.addRow(self.profile_map)  # full-width row: never clipped by the label column
        self.ppd_label = QLabel("…")
        self.profile_warn = QLabel("")
        self.profile_warn.setWordWrap(True)
        self.profile_warn.setStyleSheet("color: #b45309; font-weight: bold;")
        f2.addRow("KDE power mode:", self.ppd_label)
        f2.addRow(self.profile_warn)
        self.boost_check = QCheckBox("Fan boost (fan_boost_mode=1, loud)")
        self.boost_btn = QPushButton("Apply boost")
        self.boost_btn.clicked.connect(self.on_boost)
        h2 = QHBoxLayout()
        h2.addWidget(self.boost_check, 1)
        h2.addWidget(self.boost_btn)
        hc2 = QWidget()
        hc2.setLayout(h2)
        f2.addRow("Boost:", hc2)
        self.curve_warn = QLabel("")
        self.curve_warn.setWordWrap(True)
        self.curve_warn.setStyleSheet("color: #b45309;")
        f2.addRow(self.curve_warn)
        lay.addWidget(grp2)
        lay.addStretch(1)
        return w

    def _fancurve_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.curve_status = QLabel("…")
        self.curve_status.setWordWrap(True)
        lay.addWidget(self.curve_status)

        top = QHBoxLayout()
        self.curve_fan = QComboBox()
        self.curve_fan.addItems(["CPU fan", "GPU fan"])
        self.curve_fan.currentIndexChanged.connect(self.on_curve_fan_changed)
        self.curve_load = QPushButton("Load current")
        self.curve_load.clicked.connect(self.on_curve_load)
        top.addWidget(self.curve_fan, 1)
        top.addWidget(self.curve_load)
        lay.addLayout(top)

        grid = QGroupBox("8 points: temp °C → duty %")
        gf = QFormLayout(grid)
        self.curve_temps: list = []
        self.curve_duties: list = []
        for i in range(B.CURVE_POINTS):
            t = QSpinBox()
            t.setRange(0, 120)
            t.setSuffix(" °C")
            d = QSpinBox()
            d.setRange(0, 100)
            d.setSuffix(" %")
            self.curve_temps.append(t)
            self.curve_duties.append(d)
            row = QHBoxLayout()
            row.addWidget(QLabel(f"P{i + 1}"))
            row.addWidget(t, 1)
            row.addWidget(d, 1)
            rc = QWidget()
            rc.setLayout(row)
            gf.addRow(rc)
        lay.addWidget(grid)

        btns = QHBoxLayout()
        self.curve_apply = QPushButton("Apply curve (pkexec)")
        self.curve_apply.clicked.connect(self.on_curve_apply)
        self.curve_disable = QPushButton("Back to firmware")
        self.curve_disable.clicked.connect(lambda: self.on_curve_mode(2))
        self.curve_reset = QPushButton("Factory reset")
        self.curve_reset.clicked.connect(lambda: self.on_curve_mode(3))
        btns.addWidget(self.curve_apply)
        btns.addWidget(self.curve_disable)
        btns.addWidget(self.curve_reset)
        lay.addLayout(btns)
        hint = QLabel("Temps must be non-decreasing. Apply writes 16 values then "
                      "pwm_enable=1 (pushes to EC). Firmware reverts on reboot/sleep.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        lay.addStretch(1)
        self._curve_hwmon: str | None = None
        self._refresh_curve_status()
        return w

    def _refresh_curve_status(self):
        self._curve_hwmon = B.find_curve_hwmon()
        if self._curve_hwmon:
            n = B.curve_fan_count(self._curve_hwmon)
            names = ["CPU fan", "GPU fan"] + (["Mid fan"] if n >= 3 else [])
            self.curve_fan.clear()
            self.curve_fan.addItems(names)
            self.curve_status.setText(f"Supported: {self._curve_hwmon} ({n} fans).")
            self.curve_status.setStyleSheet("color: #16a34a; font-weight: bold;")
            for btn in (self.curve_apply, self.curve_disable, self.curve_reset, self.curve_load):
                btn.setEnabled(True)
        else:
            self.curve_status.setText(
                "Not exposed on this model/kernel: the firmware doesn't answer "
                "the fan-curve WMI probes, so the kernel creates no "
                "asus_custom_fan_curve nodes (same reason asusctl has no FanCurves). "
                "Profiles + boost on the Fans tab remain your working controls.")
            self.curve_status.setStyleSheet("color: #b45309;")
            for btn in (self.curve_apply, self.curve_disable, self.curve_reset, self.curve_load):
                btn.setEnabled(False)

    def _curve_vals(self) -> tuple[list[int], list[int]]:
        return ([s.value() for s in self.curve_temps],
                [s.value() for s in self.curve_duties])

    def on_curve_fan_changed(self, idx: int):
        self.on_curve_load()

    def on_curve_load(self):
        if not self._curve_hwmon:
            self._refresh_curve_status()
            return
        got = B.read_curve(self._curve_hwmon, self.curve_fan.currentIndex())
        if not got:
            QMessageBox.warning(self, "Fan curve", "Could not read curve (all-zero kernel defaults possible).")
            return
        temps, duties, en = got
        synth = all(t == 0 for t in temps) and any(d > 0 for d in duties)
        show_t = [30, 40, 50, 60, 70, 80, 90, 100] if synth else temps
        for s, v in zip(self.curve_temps, show_t):
            s.setValue(v)
        for s, v in zip(self.curve_duties, duties):
            s.setValue(v)
        label = B.ENABLE_LABELS.get(en, f"raw={en}")
        self.statusBar().showMessage(
            f"Loaded fan {self.curve_fan.currentIndex()} [{label}]"
            + (" — kernel temps were all-zero, showing synthetic ramp" if synth else ""), 6000)

    def on_curve_apply(self):
        if not self._curve_hwmon:
            return
        temps, duties = self._curve_vals()
        err = B.validate_curve(temps, duties)
        if err:
            QMessageBox.warning(self, "Fan curve", err)
            return
        if QMessageBox.question(self, "Apply fan curve",
                f"Write custom curve to fan {self.curve_fan.currentIndex()} and enable it?\n"
                "Firmware reverts on reboot/sleep. Keep temps sane.") != QMessageBox.Yes:
            return
        if run_privileged(B.write_curve_cmd(self._curve_hwmon,
                          self.curve_fan.currentIndex(), temps, duties), self):
            self.statusBar().showMessage("Custom fan curve applied (pwm_enable=1)", 5000)

    def on_curve_mode(self, mode: int):
        if not self._curve_hwmon:
            return
        what = {2: "firmware defaults", 3: "BIOS factory reset"}[mode]
        if run_privileged(B.curve_enable_cmd(self._curve_hwmon,
                          self.curve_fan.currentIndex(), mode), self):
            self.statusBar().showMessage(f"Fan curve -> {what} (pwm_enable={mode})", 5000)

    def _graphics_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Mode (supergfxctl — whatever your model supports)")
        f = QFormLayout(grp)
        self.gfx_box = QComboBox()
        try:
            modes = B.get_gfx_supported() or ["Hybrid", "Integrated"]
        except Exception:  # noqa: BLE001
            modes = ["Hybrid", "Integrated"]
        self.gfx_box.addItems(modes)
        self.gfx_apply = QPushButton("Switch (may need logout/reboot)")
        self.gfx_apply.clicked.connect(self.on_gfx)
        h = QHBoxLayout()
        h.addWidget(self.gfx_box, 1)
        h.addWidget(self.gfx_apply)
        hc = QWidget()
        hc.setLayout(h)
        f.addRow("Target:", hc)
        self.gfx_cur = QLabel("…")
        self.gfx_pend = QLabel("…")
        self.gfx_power = QLabel("…")
        f.addRow("Current:", self.gfx_cur)
        f.addRow("Pending:", self.gfx_pend)
        f.addRow("dGPU power:", self.gfx_power)
        lay.addWidget(grp)

        grp2 = QGroupBox("dGPU load")
        f2 = QFormLayout(grp2)
        self.nv_stats = QLabel("…")
        f2.addRow("NVIDIA:", self.nv_stats)
        row = QHBoxLayout()
        self.offload_input = QLineEdit("glxgears")
        self.offload_input.setPlaceholderText("app to run on dGPU, e.g. blender")
        self.offload_btn = QPushButton("prime-run …")
        self.offload_btn.clicked.connect(self.on_offload)
        self.nvset_btn = QPushButton("nvidia-settings")
        self.nvset_btn.clicked.connect(self.on_nvsettings)
        self.rog_btn = QPushButton("ROG Control Center")
        self.rog_btn.clicked.connect(self.on_rog)
        if not B.nvidia_present():
            self.offload_btn.setEnabled(False)
            self.offload_btn.setToolTip("No NVIDIA dGPU detected")
            self.nvset_btn.setEnabled(False)
            self.nvset_btn.setToolTip("No NVIDIA dGPU detected")
        row.addWidget(self.offload_input, 1)
        row.addWidget(self.offload_btn)
        row.addWidget(self.nvset_btn)
        row.addWidget(self.rog_btn)
        rc = QWidget()
        rc.setLayout(row)
        f2.addRow("Launch:", rc)
        lay.addWidget(grp2)
        lay.addStretch(1)
        return w

    def _battery_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        grp = QGroupBox("Battery")
        f = QFormLayout(grp)
        self.batt_bar = QProgressBar()
        self.batt_bar.setRange(0, 100)
        f.addRow("Charge:", self.batt_bar)
        self.batt_state = QLabel("…")
        self.batt_health = QLabel("…")
        self.batt_sysfs = QLabel("…")
        self.batt_asus = QLabel("…")
        self.batt_hwdb = QLabel("…")
        self.batt_upower = QLabel("…")
        self.batt_warn = QLabel("")
        self.batt_warn.setWordWrap(True)
        self.batt_warn.setStyleSheet("color: #b45309; font-weight: bold;")
        for lab in (self.batt_sysfs, self.batt_asus, self.batt_hwdb, self.batt_upower):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setWordWrap(True)
        f.addRow("State:", self.batt_state)
        f.addRow("Health:", self.batt_health)
        f.addRow("Firmware (real):", self.batt_sysfs)
        f.addRow("asusctl:", self.batt_asus)
        f.addRow("udev hwdb:", self.batt_hwdb)
        f.addRow("UPower live:", self.batt_upower)
        f.addRow(self.batt_warn)
        lay.addWidget(grp)

        grp2 = QGroupBox("Charge limit (sets firmware + KDE/UPower together)")
        f2 = QFormLayout(grp2)
        self.limit_slider = QSlider(Qt.Horizontal)
        self.limit_slider.setRange(20, 100)
        self.limit_slider.setValue(80)
        self.limit_slider.setTickInterval(5)
        self.limit_slider.setTickPosition(QSlider.TicksBelow)
        self.limit_val = QLabel("80%")
        self.limit_slider.valueChanged.connect(lambda v: self.limit_val.setText(f"{v}%"))
        self.limit_apply = QPushButton("Set limit")
        self.limit_apply.clicked.connect(self.on_limit)
        h = QHBoxLayout()
        h.addWidget(self.limit_slider, 1)
        h.addWidget(self.limit_val)
        h.addWidget(self.limit_apply)
        hc = QWidget()
        hc.setLayout(h)
        f2.addRow("Limit:", hc)
        hint = QLabel("Takes effect after asusd writes sysfs. If already above the limit, "
                      "unplug to drain below it first. Re-check with Refresh.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        f2.addRow(hint)
        lay.addWidget(grp2)
        lay.addStretch(1)
        return w

    # ----- actions -----
    def on_profile(self):
        target = self.profile_box.currentText()
        # Side 1a — active now (asusd via D-Bus, no auth needed)
        if not run_privileged(B.set_profile_cmd(target), self):
            return
        # Side 1b — persist to the CURRENT power source mapping (-a/-b) so
        # asusd autoswitch remembers the choice instead of reverting it.
        src = "ac" if B.on_ac_power() in (True, None) else "battery"
        if not run_privileged(B.set_profile_source_cmd(target, src), self):
            self.statusBar().showMessage(
                f"Profile active -> {target} but failed to save {src} mapping", 6000)
            self.refresh(sync_inputs=True)
            return
        # Side 2 — KDE power mode (power-profiles-daemon, no auth needed)
        if not run_privileged(B.set_ppd_cmd(target), self):
            self.statusBar().showMessage(
                f"ASUS profile -> {target} (+{src} map) but KDE power mode update failed", 6000)
            self.refresh(sync_inputs=True)
            return
        self.statusBar().showMessage(
            f"Profile -> {target} (active + {src} map) + {B.ASUS_TO_PPD[target]} (KDE)", 5000)
        self.refresh(sync_inputs=True)

    def on_boost(self):
        if run_privileged(B.set_boost_cmd(self.boost_check.isChecked()), self):
            self.statusBar().showMessage("Boost updated", 4000)
            self.refresh()

    def on_gfx(self):
        if run_privileged(B.set_gfx_cmd(self.gfx_box.currentText()), self):
            self.statusBar().showMessage("Graphics mode change requested — check Pending, logout/reboot if asked", 6000)
            self.refresh()

    def on_limit(self):
        target = self.limit_slider.value()
        # Side 1 — firmware (no auth needed, asusd via D-Bus)
        if not run_privileged(B.set_limit_cmd(target), self):
            return
        # Side 2 — UPower/KDE hwdb (needs root: /etc + hwdb rebuild)
        if not run_privileged(B.hwdb_set_cmd(target), self):
            self.statusBar().showMessage(
                f"Firmware limit -> {target}% but KDE/hwdb update was cancelled — sides still differ", 6000)
            self.refresh(sync_inputs=True)
            return
        self.statusBar().showMessage(
            f"Charge limit -> {target}% on both sides (firmware + KDE/UPower)", 5000)
        self.refresh(sync_inputs=True)

    def on_offload(self):
        app = self.offload_input.text().strip() or "glxgears"
        try:
            subprocess.Popen(B.prime_run_cmd(app))
            self.statusBar().showMessage(f"Launched prime-run {app}", 4000)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Launch failed", str(e))

    def on_nvsettings(self):
        try:
            subprocess.Popen(["prime-run", "nvidia-settings"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Launch failed", str(e))

    def on_rog(self):
        try:
            subprocess.Popen(["rog-control-center"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Launch failed", str(e))

    # ----- refresh -----
    def refresh(self, sync_inputs=False):
        """Auto-refresh readouts. Input widgets are only synced on the
        first call and on explicit Refresh clicks, never by the timer."""
        try:
            fan = B.get_fan_status()
        except Exception:  # noqa: BLE001
            fan = None
        try:
            gpu = B.get_gpu_status()
        except Exception:  # noqa: BLE001
            gpu = None
        try:
            bat = B.get_battery()
        except Exception:  # noqa: BLE001
            bat = None

        if gpu:
            self.dot.set_state(gpu.active)
            self.gpu_label.setText(f"GPU: {gpu.active}  ({gpu.mode})")
            self.gpu_detail.setText(gpu.active_detail)
            self.ov_mode.setText(f"{gpu.mode}  (supported: {', '.join(gpu.supported)})")
            self.ov_renderer.setText(gpu.default_renderer)
            ntxt = (f"{gpu.nvidia_temp}C, {gpu.nvidia_util}%, {gpu.nvidia_power}W, {gpu.nvidia_procs} procs"
                    if gpu.nvidia_temp is not None else "powered down / unreachable")
            self.ov_nvidia.setText(ntxt)
            self.gfx_cur.setText(gpu.mode)
            pend = " ".join(x for x in [gpu.pend_action, gpu.pend_mode] if x) or "none — no logout/reboot needed"
            self.gfx_pend.setText(pend)
            self.gfx_power.setText(gpu.power_status or "n/a")
            self.nv_stats.setText(f"{ntxt} | {gpu.active_detail}")
            if sync_inputs or not self._inputs_synced:
                modes = [self.gfx_box.itemText(i) for i in range(self.gfx_box.count())]
                if gpu.mode in modes:
                    self.gfx_box.setCurrentText(gpu.mode)

        if fan:
            rpm = " / ".join(x for x in (
                f"CPU {fan.cpu_rpm} RPM" if fan.cpu_rpm else None,
                f"GPU {fan.gpu_rpm} RPM" if fan.gpu_rpm else None,
                f"Mid {fan.mid_rpm} RPM" if fan.mid_rpm else None) if x) or "n/a"
            self.ov_fans.setText(rpm)
            self.fan_cpu.setText(f"{fan.cpu_rpm} RPM" if fan.cpu_rpm else "n/a")
            self.fan_gpu.setText(f"{fan.gpu_rpm} RPM" if fan.gpu_rpm else "n/a")
            self.fan_mid.setText(f"{fan.mid_rpm} RPM" if fan.mid_rpm else "n/a (2-fan model)")
            self.ov_temps.setText(f"pkg {fan.package_temp}C / core0 {fan.cpu_temp}C")
            self.temp_pkg.setText(f"{fan.package_temp} °C" if fan.package_temp else "n/a")
            self.temp_core.setText(f"{fan.cpu_temp} °C" if fan.cpu_temp else "n/a")
            self.curve_warn.setText("" if fan.fan_curve_supported else
                "Note: 8-point curves live on the Fan Curve tab (dormant on this firmware) — use profiles + boost.")
            try:
                ppd = B.get_ppd_profile()
            except Exception:  # noqa: BLE001
                ppd = "unknown"
            self.ppd_label.setText(ppd)
            self.ov_profile.setText(f"{fan.profile} / KDE {ppd} (boost={fan.boost})")
            match = B.PPD_TO_ASUS.get(ppd) == fan.profile
            self.profile_warn.setText("" if match else
                "Sides differ — press Apply profile to re-align ASUS + KDE.")
            try:
                pmap = B.get_profile_map()
                ac_now = B.on_ac_power()
                src_now = "AC" if ac_now else ("battery" if ac_now is False else "?")
                self.profile_map.setText(
                    f"Autoswitch map — AC → {pmap['ac']} • Battery → {pmap['battery']} • now on {src_now}")
            except Exception:  # noqa: BLE001
                pass
            if sync_inputs or not self._inputs_synced:
                if fan.profile in ("Quiet", "Balanced", "Performance"):
                    self.profile_box.setCurrentText(fan.profile)
                self.boost_check.setChecked(fan.boost == 1)

        if bat:
            self.batt_bar.setValue(bat.percent or 0)
            self.batt_bar.setFormat(f"{bat.percent}% — {bat.state}" if bat.percent is not None else "n/a")
            self.ov_batt.setText(f"{bat.percent}% {bat.state} (limit {bat.limit_now}%, health {bat.health_pct}%)")
            self.batt_state.setText(f"{bat.percent}% — {bat.state} @ {bat.rate_w}W" if bat.percent is not None else "n/a")
            self.batt_health.setText(f"{bat.health_pct}%" if bat.health_pct else "n/a")
            self.batt_sysfs.setText(f"{bat.limit_now}%" if bat.limit_now is not None else "n/a")
            self.batt_asus.setText(bat.limit_asusctl)
            self.batt_hwdb.setText(f"start {bat.hwdb_start} / end {bat.hwdb_end} — {bat.hwdb_source}")
            self.batt_upower.setText(f"start {bat.upower_start} / end {bat.upower_end} (live daemon)")
            if (sync_inputs or not self._inputs_synced) and bat.limit_now is not None:
                # Never steal the slider while the user is dragging it.
                if not self.limit_slider.isSliderDown():
                    self.limit_slider.setValue(bat.limit_now)
            ends = {str(bat.limit_now), bat.hwdb_end.rstrip("%"), bat.upower_end.rstrip("%")}
            ends.discard("n/a")
            ends.discard("None")
            self.batt_warn.setText("" if len(ends) <= 1 else
                "Sides differ — press Set limit again to re-align firmware + KDE/UPower.")
            self._inputs_synced = True


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TUF Control")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
