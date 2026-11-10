# Pilot: Russian HTTPS entrance to the existing US guest proxy

**Status:** prepared, **not deployed**. The old Timeweb US VPS at `186.246.18.103` is untouched.

## Traffic path

`Windows user -> TLS -> ru.paruslegal.ru:443 (new RU VPS, Nginx stream TLS termination) -> TLS -> paruslegal.ru:43128 (existing US stunnel + 3proxy) -> destination`.

The Russian VPS **does not issue credentials**, change access duration or store the guest database. Credentials are verified on the existing US service, and the website sees the existing US egress IP. The RU gateway does not guarantee availability if the RU-to-US traffic is filtered.

## Requirements

1. Create a **new**, clean Ubuntu 24.04 Timeweb VPS in Moscow (or another Russian location). Do not touch the existing US VPS. A minimal VPS is enough for a single-user pilot.
2. Add DNS `A ru.paruslegal.ru -> <public RU VPS IP>`. Wait until it resolves.
3. Open incoming TCP 22 (SSH), TCP 80 (Let's Encrypt HTTP-01), and TCP 443 (HTTPS ingress), on the cloud firewall if enabled. Restrict SSH to your IP wherever possible.
4. Copy the script `ops/install_ru_guest_gateway.sh` to **the new RU VPS only**. Run as root, setting `CONFIRM_NEW_RU_VPS=yes` and `ACME_EMAIL` in the shell's environment. Don't paste the certificate email or secrets into chat.
5. The installer refuses existing Nginx and refuses running on the known US public IP; it doesn't install on the American server.
6. Run `nginx -t; systemctl status nginx --no-pager; ss -lnt | grep :443` on the RU VPS.

## Test without affecting Vesya or old Chrome

On Windows with a reasonably recent curl that supports HTTPS proxies (check `curl.exe -V`), get the existing **username only** from Vesya's `/proxy_manual`:

```powershell
curl.exe -v -x https://ru.paruslegal.ru:443 --proxy-user user003 https://api.ipify.org/
```

Use your actual username; curl should prompt for its password. **Don't share it in chat.** A working gateway should show the **US egress IP** `186.246.18.103` instead of the RU VPS IP.

Only after a successful end-to-end test should we build an optional `Vesya_ChatGPT_RU.exe` which connects to `ru.paruslegal.ru:443` while preserving the original client and all account data.

## Security

- Public port 443 only (plus 80 for certificate renewal); the proxy itself continues to authenticate on the existing US service.
- Nginx verifies the upstream certificate against `paruslegal.ru`. Do not use `proxy_ssl_verify off`.
- This is not an invisible tunnel. RU->US IP, traffic patterns, and TLS metadata can still be filtered. Additional latency is possible.
