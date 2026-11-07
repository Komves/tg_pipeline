#!/usr/bin/env bash
# Update only the generic Windows client. Do not touch proxy daemons or accounts.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Запусти от root"; exit 1; }

SRC=/usr/local/src/vesya-proxy-client
DST=/usr/local/share/vesya-proxy/VesyaProxyClient.exe
[[ -s "$DST" ]] || { echo "Клиент не найден: $DST"; exit 1; }
command -v go >/dev/null || { echo "Go не установлен"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL --retry 2 --max-time 30 \
  https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/guest_proxy_client/main.go \
  -o "$TMP/main.go"
curl -fsSL --retry 2 --max-time 30 \
  https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/guest_proxy_client/go.mod \
  -o "$TMP/go.mod"
(
  cd "$TMP"
  CGO_ENABLED=0 GOOS=windows GOARCH=amd64 GOTOOLCHAIN=local GOMAXPROCS=2 \
    go build -trimpath -ldflags="-s -w -H windowsgui" -o "$TMP/VesyaProxyClient.new.exe" .
)
[[ -s "$TMP/VesyaProxyClient.new.exe" ]] || { echo "Ошибка сборки"; exit 1; }
[[ "$(head -c 2 "$TMP/VesyaProxyClient.new.exe")" == "MZ" ]] || { echo "Неверный формат Windows EXE"; exit 1; }

# Atomic install, with one restore point.
install -d -m 0755 "$(dirname "$DST")" "$SRC"
cp -p "$DST" "${DST}.backup" 
cp "$TMP/main.go" "$SRC/main.go"
cp "$TMP/go.mod" "$SRC/go.mod"
install -m 0644 "$TMP/VesyaProxyClient.new.exe" "${DST}.new"
mv -f "${DST}.new" "$DST"

echo "Готово: новый клиент $(stat -c %s "$DST") байт"
echo "Личный SOCKS5, гостевой прокси, API, база и Telegram-бот не изменялись."
echo "Для проверки запроси /proxy_status у Веси и скачай новый EXE."
