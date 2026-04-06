#!/bin/bash

set -e

echo "[*] Updating packages..."
sudo apt update -y

echo "[*] Installing Python + pip..."
sudo apt install -y python3 python3-pip git

echo "[*] Upgrading pip..."
python3 -m pip install --upgrade pip --user

echo "[*] Installing Penzer globally..."
python3 -m pip install --user -e .

LOCAL_BIN="$HOME/.local/bin"

if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    SHELL_NAME=$(basename "$SHELL")

    if [ "$SHELL_NAME" = "bash" ]; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    elif [ "$SHELL_NAME" = "zsh" ]; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
    fi

    export PATH="$LOCAL_BIN:$PATH"
fi

echo ""
echo "[✓] Penzer installed successfully"
echo ""
echo "Run:"
echo "penzer"
echo ""
echo "If command fails, restart terminal or run:"
echo "source ~/.bashrc"
