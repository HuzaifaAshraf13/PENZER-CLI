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

echo "[*] Checking Python..."

PYTHON_CMD="python3"

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "[!] Python 3.14 is required but python3 was not found."
  exit 1
fi

PYTHON_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')

if [ "$PYTHON_VERSION" != "3.14" ]; then
  echo "[!] Penzer requires Python 3.14+."
  echo "[!] Found Python $PYTHON_VERSION."
  exit 1
fi

echo "[✓] Python $PYTHON_VERSION detected."

if [ -n "$SUDO" ]; then
  echo "[*] Updating packages..."
  $SUDO apt update -y || true

  echo "[*] Installing system dependencies..."
  $SUDO apt install -y \
    python3-venv \
    python3-dev \
    python3-pip \
    git \
    xdotool \
    imagemagick \
    wmctrl || true
fi

if [ ! -x "$VENV_PYTHON" ]; then
  echo "[*] Creating project virtual environment..."
  "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

echo "[*] Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip

echo "[*] Installing Penzer into the project virtual environment..."
"$VENV_PYTHON" -m pip install -e "$ROOT_DIR"

echo "[*] Creating launcher in $LOCAL_BIN..."
mkdir -p "$LOCAL_BIN"

cat > "$LOCAL_BIN/penzer" <<EOF_LAUNCHER
#!/bin/bash
export PYTHONPATH="$ROOT_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV_PYTHON" "$ROOT_DIR/cli.py" "\$@"
EOF_LAUNCHER

chmod +x "$LOCAL_BIN/penzer"

if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
  export PATH="$LOCAL_BIN:$PATH"

  for PROFILE in \
    "$HOME/.bashrc" \
    "$HOME/.bash_profile" \
    "$HOME/.profile" \
    "$HOME/.zshrc"
  do
    if [ -f "$PROFILE" ]; then
      grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$PROFILE" 2>/dev/null \
        || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE"
    fi
  done
fi

if [ -x "$LOCAL_BIN/penzer" ]; then
  echo "[✓] Installed launcher: $LOCAL_BIN/penzer"
fi

echo ""
echo "========================================"
echo "       Penzer installed successfully"
echo "========================================"
echo ""
echo "Python:      $PYTHON_VERSION"
echo "Environment: $VENV_DIR"
echo "Launcher:    $LOCAL_BIN/penzer"
echo ""
echo "Run:"
echo "  penzer"
echo ""
echo "If 'penzer' is not found, restart your terminal or run:"
echo "  source ~/.bashrc"
echo ""