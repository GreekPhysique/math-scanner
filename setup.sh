#!/bin/bash
# Math Scanner — One-command setup
set -e

cd "$(dirname "$0")"

echo "=== Math Scanner Setup ==="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required. Install Xcode Command Line Tools:"
    echo "  xcode-select --install"
    exit 1
fi

# Check Swift
if ! command -v swiftc &>/dev/null; then
    echo "Error: Swift compiler required. Install Xcode Command Line Tools:"
    echo "  xcode-select --install"
    exit 1
fi

# Install Python dependencies
echo "[1/3] Installing Python packages..."
pip3 install --user sympy Pillow mss 2>&1 | tail -1

# Compile OCR helper
echo "[2/3] Compiling OCR helper..."
swiftc -o ocr_helper ocr_helper.swift -framework Vision -framework AppKit

echo "[3/3] Done!"
echo ""
echo "To run:  python3 math_solver.py"
echo ""
echo "NOTE: You must grant Screen Recording permission to your terminal/app."
echo "  System Settings → Privacy & Security → Screen Recording"
echo ""

# Ask to launch
read -p "Launch now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    python3 math_solver.py
fi
