"""Qt (PySide6) graphical interface: ``xeno gui`` / ``xeno-control``.

Runs as a normal user. Privileged writes (profile, fans, battery) are done by
re-invoking the CLI through ``pkexec`` so the desktop asks for the password.
"""
from __future__ import annotations

import os
import subprocess
import sys

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QApplication, QButtonGroup, QComboBox, QGridLayout, QGroupBox,
        QLabel, QMainWindow, QMessageBox, QPushButton, QSlider,
        QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )
except ImportError:  # pragma: no cover
    QApplication = None

from . import __version__, devices, laptop

STYLE = """
QWidget { background: #0d0f14; color: #d8dee9; font-size: 13px; }
QTabWidget::pane { border: 1px solid #1f2430; border-radius: 8px; }
QTabBar::tab { background: #141821; padding: 10px 22px; margin-right: 2px;
               border-top-left-radius: 8px; border-top-right-radius: 8px; }
QTabBar::tab:selected { background: #1f2430; color: #00d1ff; }
QGroupBox { border: 1px solid #1f2430; border-radius: 8px; margin-top: 14px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #8892a6; }
QPushButton { background: #1a1f2b; border: 1px solid #2a3142; border-radius: 6px; padding: 10px 14px; }
QPushButton:hover { border-color: #00d1ff; }
QPushButton:checked { background: #003a4a; border-color: #00d1ff; color: #00d1ff; font-weight: bold; }
QSlider::groove:horizontal { height: 6px; background: #1f2430; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; background: #00d1ff; border-radius: 8px; }
QTextEdit, QComboBox, QSpinBox { background: #141821; border: 1px solid #2a3142; border-radius: 6px; padding: 4px; }
"""


def run_cli(*args: str) -> tuple[bool, str]:
    """Run a CLI command, escalating with pkexec when not root."""
    cmd = [sys.executable, "-m", "xeno_control.cli", *args]
    if os.geteuid() != 0:
        cmd = ["pkexec", *cmd]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


class PowerTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        info = laptop.system_info()
        header = QLabel(f"<h2>{info.product or 'Alienware'}</h2>"
                        f"<span style='color:#8892a6'>BIOS {info.bios or '?'} · alienware-wmi "
                        f"{'ativo' if info.alienware_wmi_loaded else 'não carregado'}</span>")
        layout.addWidget(header)

        box = QGroupBox("Modo de energia / térmico")
        grid = QGridLayout(box)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        choices = laptop.profile_choices()
        if not choices:
            grid.addWidget(QLabel("platform_profile indisponível. Veja o README (force_platform_profile)."), 0, 0)
        for i, name in enumerate(choices):
            btn = QPushButton(laptop.PROFILE_LABELS.get(name, name))
            btn.setCheckable(True)
            btn.setProperty("profile", name)
            btn.clicked.connect(lambda _=False, n=name: self.apply(n))
            self.group.addButton(btn)
            grid.addWidget(btn, i // 3, i % 3)
        layout.addWidget(box)

        bats = laptop.batteries()
        if bats:
            bat = bats[0]
            bbox = QGroupBox(f"Bateria ({bat.name})")
            bl = QGridLayout(bbox)
            self.bat_label = QLabel()
            bl.addWidget(self.bat_label, 0, 0, 1, 4)
            if bat.charge_types:
                self.mode = QComboBox()
                self.mode.addItems(bat.charge_types)
                bl.addWidget(QLabel("Modo de carga"), 1, 0)
                bl.addWidget(self.mode, 1, 1)
                b = QPushButton("Aplicar modo")
                b.clicked.connect(lambda: self._cli("battery", "mode", self.mode.currentText()))
                bl.addWidget(b, 1, 2)
            if bat.end_threshold is not None or bat.backend == "smbios":
                lo = 50 if bat.backend == "smbios" else 0  # limites aceitos pela BIOS Dell
                self.start = QSpinBox(); self.start.setRange(lo, 95); self.start.setValue(bat.start_threshold or 50)
                self.end = QSpinBox(); self.end.setRange(lo + 5, 100); self.end.setValue(bat.end_threshold or 80)
                bl.addWidget(QLabel("Carregar de / até (%)"), 2, 0)
                bl.addWidget(self.start, 2, 1)
                bl.addWidget(self.end, 2, 2)
                b = QPushButton("Aplicar limites")
                b.clicked.connect(lambda: self._cli("battery", "limit", str(self.start.value()), str(self.end.value())))
                bl.addWidget(b, 2, 3)
            layout.addWidget(bbox)
        else:
            self.bat_label = None

        layout.addStretch(1)
        self.refresh()

    def _cli(self, *args: str) -> None:
        ok, out = run_cli(*args)
        if not ok:
            QMessageBox.warning(self, "Xeno Control", out or "Falhou")
        self.refresh()

    def apply(self, name: str) -> None:
        self._cli("profile", "set", name)

    def refresh(self) -> None:
        current = laptop.current_profile()
        for btn in self.group.buttons():
            btn.setChecked(btn.property("profile") == current)
        if self.bat_label is not None:
            bats = laptop.batteries()
            if bats:
                b = bats[0]
                note = f"<br><span style='color:#8892a6'>{b.note}</span>" if b.note else ""
                self.bat_label.setText(f"<b>{b.capacity}%</b> · {b.status} · modo {b.charge_type or '-'}{note}")
                if hasattr(self, "mode") and b.charge_type in b.charge_types:
                    self.mode.setCurrentText(b.charge_type)


class ThermalTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.readings = QLabel()
        self.readings.setTextFormat(Qt.RichText)
        self.readings.setAlignment(Qt.AlignTop)
        layout.addWidget(self.readings)

        boosts = [s for s in laptop.read_sensors() if s.kind == "boost"]
        if boosts:
            box = QGroupBox("Controle manual das ventoinhas (ativa o perfil 'Personalizado')")
            bl = QGridLayout(box)
            for row, s in enumerate(boosts):
                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 255)
                slider.setValue(int(s.value or 0))
                value = QLabel(str(slider.value()))
                slider.valueChanged.connect(lambda v, lbl=value: lbl.setText(str(v)))
                apply = QPushButton("Aplicar")
                apply.clicked.connect(lambda _=False, i=s.index, sl=slider: self.set_boost(i, sl.value()))
                bl.addWidget(QLabel(s.label), row, 0)
                bl.addWidget(slider, row, 1)
                bl.addWidget(value, row, 2)
                bl.addWidget(apply, row, 3)
            layout.addWidget(box)
        layout.addStretch(1)
        self.refresh()

    def set_boost(self, index: int, value: int) -> None:
        if laptop.current_profile() != "custom" and "custom" in laptop.profile_choices():
            run_cli("profile", "set", "custom")
        ok, out = run_cli("fan", str(index), str(value))
        if not ok:
            QMessageBox.warning(self, "Xeno Control", out)

    def refresh(self) -> None:
        rows = []
        for s in laptop.read_sensors():
            if s.kind not in ("fan", "temp") or s.value is None:
                continue
            color = "#00d1ff"
            if s.kind == "temp":
                color = "#ff5370" if s.value >= 90 else "#ffcb6b" if s.value >= 75 else "#c3e88d"
            rows.append(f"<tr><td style='color:#8892a6;padding-right:16px'>{s.chip}</td>"
                        f"<td style='padding-right:24px'>{s.label}</td>"
                        f"<td style='color:{color};font-weight:bold' align='right'>{s.value:.0f} {s.unit}</td></tr>")
        self.readings.setText("<table>" + "".join(rows) + "</table>" if rows
                              else "Nenhum sensor hwmon encontrado.")


class PeripheralsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        btn = QPushButton("Atualizar")
        btn.clicked.connect(self.refresh)
        layout.addWidget(btn)
        self.refresh()

    def refresh(self) -> None:
        parts = []
        for p in devices.peripherals():
            status = p.info.status if p.info else "dispositivo desconhecido"
            nodes = ", ".join(i.node for i in p.interfaces)
            bat = ""
            if p.supports_battery:
                r = p.battery()
                bat = (f"<p style='font-size:20px'>🔋 <b>{r.level}%</b></p>" if r.level is not None
                       else f"<p>🔋 bateria: {r.error}</p>")
            parts.append(f"<h3>{p.title}</h3>{bat}<p style='color:#8892a6'>{p.vid:04x}:{p.pid:04x} · {nodes}"
                         f"<br>{status}</p>")
        if not parts:
            parts.append("<p>Nenhum periférico Alienware/Dell encontrado.</p>")
        parts.append("<p style='color:#8892a6'>DPI, RGB, bateria do mouse, EQ e sidetone dependem do mapeamento do "
                     "protocolo USB. Veja docs/REVERSE_ENGINEERING.md e use <code>xeno hid monitor</code>.</p>")
        self.text.setHtml("".join(parts))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Xeno Control {__version__}")
        self.resize(760, 560)
        tabs = QTabWidget()
        self.power = PowerTab()
        self.thermal = ThermalTab()
        tabs.addTab(self.power, "Energia")
        tabs.addTab(self.thermal, "Térmico")
        tabs.addTab(PeripheralsTab(), "Periféricos")
        self.setCentralWidget(tabs)
        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(2000)

    def tick(self) -> None:
        self.thermal.refresh()
        self.power.refresh()


def main() -> int:
    if QApplication is None:
        print("PySide6 não instalado: pip install 'xeno-control[gui]'", file=sys.stderr)
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName("Xeno Control")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
