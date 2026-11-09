#!/usr/bin/env bash
# Xeno Control installer (Linux). Run from the repository root.
set -euo pipefail

cd "$(dirname "$0")"

if command -v pipx >/dev/null 2>&1; then
    pipx install --force ".[gui]"
else
    echo "pipx não encontrado; instalando com pip --user"
    python3 -m pip install --user --break-system-packages ".[gui]"
fi

BIN="$(command -v xeno || echo "$HOME/.local/bin/xeno")"

echo "Instalando regras udev (precisa de sudo)..."
sudo install -m 644 data/70-xeno-control.rules /etc/udev/rules.d/70-xeno-control.rules
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw

echo "Instalando serviço que restaura o perfil no boot..."
sed "s#/usr/bin/env xeno#${BIN}#" data/xeno-control-restore.service \
    | sudo tee /etc/systemd/system/xeno-control-restore.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable xeno-control-restore.service

install -Dm 644 data/xeno-control.desktop "$HOME/.local/share/applications/xeno-control.desktop"

echo
echo "Pronto! Rode 'xeno status' ou abra 'Xeno Control' no menu."
