# GPT Windows installer — security review

**Status: PRE-RELEASE. Do not send to customers. Do not set GPT_INSTALLER_URL.**

The previous unsigned loose GPT.exe caused a Kaspersky Endpoint Security alert
\`PDM:Trojan.Win32.Generic\` on 2026-11-18. That build was withdrawn.

## What changed

- Launcher no longer copies its own executable into AppData.
- Launcher no longer invokes PowerShell or secretly creates shortcuts.
- Launcher never starts a second copy of itself as part of installation.
- Standard per-user Inno Setup installer installs \`%LOCALAPPDATA%\GPT\GPT.exe\`,
  creates the \`GPT\` desktop/Start Menu shortcuts, and registers an uninstaller.
- Installer offers **optional** launch after setup; it never silently starts the app.
- Chrome runs in a **separate** \`%LOCALAPPDATA%\GPT\ChromeProfile\` profile,
  with HTTP/2 and QUIC disabled; normal Chrome and legacy /proxy are unchanged.
- Credentials stay in \`%LOCALAPPDATA%\GPT\account.dat\` encrypted by Windows
  DPAPI for that Windows user; no customer password is embedded in the binary.
- No system-wide proxy, service, driver, autorun entry, browser extension,
  firewall exception, or antivirus exclusion is created.

## Automated checks

Windows GitHub Actions builds the launcher and Inno Setup EXE on Windows,
runs a static check for risky legacy operations, scans both files with
an **active Microsoft Defender**, then silently installs, verifies shortcuts,
and uninstalls the app.

First successful review build: workflow run **44243874975** on 2026-11-18.
Defender signatures **1.463.256.0** reported no detections in the custom scans;
install/shortcut/uninstall smoke tests passed.

SHA256 of the review installer \`GPT-Setup-Review.exe\`:
\`7fd706e0bb182294ce686eb2d1a92fdb996d4849d9e024c13d4da09b558d6f76\`

SHA256 of the compiled app \`GPT.exe\`:
\`b48b22b96c296d051c67fc35b16072aa11a1fc1ad1246a494e3c3e0e80f50518\`

These tests **do not demonstrate Kaspersky compatibility** and do not replace
a vendor false-positive analysis or digital signing.

## Kaspersky validation gate

1. Test only on a Windows machine with Kaspersky Endpoint Security enabled,
   *not* on a client installation.
2. Extract the private review ZIP, right-click \`GPT-Setup-Review.exe\` and
   choose Kaspersky's file scan. Do **not** disable Kaspersky or add exclusions.
3. If clean, perform a controlled installation on an approved test PC while
   monitoring alerts. No real client credentials should be used for tests.
4. If Kaspersky flags the file, retain the detection details and submit the
   exact sample to Kaspersky for analysis. Do not publish or override the alert.
5. Only after AV review and preferably code signing, publish a new official
   installer and update Vesya's \`GPT_INSTALLER_URL\`.

The GitHub Actions workflow intentionally **cannot publish** any GitHub Release.
