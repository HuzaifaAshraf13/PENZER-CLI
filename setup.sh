#!/bin/bash
set -e

if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/env"
VENV_PYTHON="$VENV_DIR/bin/python"
LOCAL_BIN="$HOME/.local/bin"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "[*] Creating project virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

PYTHON_BIN="$VENV_PYTHON"

if [ -n "$SUDO" ]; then
  echo "[*] Updating packages..."
  $SUDO apt update -y || true
  echo "[*] Installing system dependencies..."
  $SUDO apt install -y xdotool imagemagick wmctrl python3-dev python3-pip git || true
fi

echo "[*] Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip

echo "[*] Installing Penzer into the project virtual environment..."
"$PYTHON_BIN" -m pip install -e "$ROOT_DIR"

echo "[*] Creating launcher in $LOCAL_BIN..."
mkdir -p "$LOCAL_BIN"
cat > "$LOCAL_BIN/penzer" <<EOF_LAUNCHER
#!/bin/bash
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PYTHON" "$ROOT_DIR/cli.py" "\$@"
EOF_LAUNCHER
chmod +x "$LOCAL_BIN/penzer"

if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
    for PROFILE in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.zshrc"; do
        if [ -n "$PROFILE" ] && [ -f "$PROFILE" ]; then
            grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$PROFILE" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE"
        fi
    done
fi

if [ -x "$LOCAL_BIN/penzer" ]; then
  echo "[*] Installed script: $LOCAL_BIN/penzer"
fi

echo ""
echo "[✓] Penzer installed successfully"
echo ""
echo "Run:"
echo "penzer"
echo ""
echo "If command fails, restart terminal or run:"
echo "source ~/.bashrc"