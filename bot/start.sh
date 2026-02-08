#!/usr/bin/env bash
set -e

echo "[boot] starting: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[boot] pwd: $(pwd)"
echo "[boot] python: $(python --version)"

# важно: -u = unbuffered stdout/stderr, чтобы логи сразу появлялись в Render
exec python -u main.py