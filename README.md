# Blue Riiot Pool Monitor — LoxBerry Plugin

A [LoxBerry](https://www.loxberry.de/) plugin that reads your **Blue Riiot / Blue Connect** pool
sensor data from the Blue Riiot cloud and forwards the live values (pH, water temperature, ORP,
and more) to a **Loxone Miniserver** via UDP.

It authenticates against the Blue Riiot cloud API using AWS Signature V4, auto-detects your pool
and Blue device, encrypts your password locally, and polls on a configurable interval.

## Features

- 🔐 **Secure** — your Blue Riiot password is encrypted at rest (Fernet/AES); only the encrypted
  token is stored on disk.
- 🔎 **Auto-discovery** — pool ID and Blue device serial are detected automatically and updated
  if you swap devices in your account.
- 🌊 **Device values only** — readings come straight from the Blue Connect device
  (issuer `sigfox`/`blue`); test-strip entries are filtered out.
- 📡 **Loxone integration** — values are sent as UDP datagrams (`key=value`) to the Miniserver.
  The Miniserver IP is read automatically from LoxBerry's system settings.
- 🖥️ **Web frontend** — tabbed UI (Data / Config / Log) showing live measurements, weather,
  device status and a live log.
- ⏱️ **Scheduled polling** — runs via LoxBerry cron, honouring your configured interval.

## Installation

1. Download the latest release ZIP (or build one — see below).
2. In LoxBerry: **Plugin installation** → upload the ZIP.
3. The plugin installs its Python dependencies automatically
   (`python3-requests`, `python3-cryptography` via apt).
4. Open the plugin, go to the **Config** tab, enter your Blue Riiot email + password,
   set the UDP port, and click **Save**.
5. Click **Fetch now** once — the password is encrypted and your pool/device are detected.

## Getting the values into Loxone

The plugin sends one UDP datagram per value (`key=value`) to your Miniserver on the configured
UDP port (default `7777`).

In **Loxone Config**:

1. Add a **Virtual UDP Input** and set its UDP receive port to the same value (e.g. `7777`).
2. For each value add a **Virtual UDP Input Command** with a *command recognition* string:

   | Value             | Command recognition          |
   |-------------------|------------------------------|
   | Water temperature | `temperature=\v`             |
   | pH                | `ph=\v`                      |
   | ORP (Redox)       | `orp=\v`                     |
   | Free chlorine     | `fcl=\v`                     |
   | TDS               | `tds=\v`                     |
   | Battery           | `battery=\v`                 |
   | Air temperature   | `temperature_current=\v`     |

   Only values your device actually reports are sent.

## Configuration

| Setting          | Description                                                            |
|------------------|------------------------------------------------------------------------|
| Email / Password | Your Blue Riiot account credentials.                                   |
| UDP port         | Port of your Loxone Virtual UDP Input (default `7777`).                |
| Polling interval | Seconds between fetches. The Blue device sends roughly every 72 min.   |

The Miniserver IP is taken from LoxBerry's system settings (**Settings → Miniserver**) — no manual
entry needed.

## Building a plugin ZIP

The archive must use forward-slash paths and have `plugin.cfg` at the root. On Windows
(PowerShell), build it with .NET to guarantee correct separators:

```powershell
$proj = "."
$out  = "blueriiot.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($out, 'Create')
Get-ChildItem $proj -Recurse -File |
  Where-Object { $_.FullName -notmatch '\\(data|\.git)\\' -and $_.Extension -ne '.zip' } |
  ForEach-Object {
    $rel = $_.FullName.Substring((Resolve-Path $proj).Path.Length + 1).Replace('\','/')
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $rel, 'Optimal') | Out-Null
  }
$zip.Dispose()
```

On Linux/macOS a plain `zip -r blueriiot.zip . -x '*.git*' 'data/*' '*.zip'` works.

## Project layout

```
plugin.cfg                       LoxBerry plugin metadata
dpkg/apt                         apt packages installed on setup
cron/cron.05min/blueriiot        polling job (honours configured interval)
bin/fetch_pool.py                main fetcher (API, encryption, UDP)
config/pool.cfg                  config template (no credentials committed)
webfrontend/htmlauth/index.cgi   tabbed web UI
icons/icon_*.png                 plugin icons
```

## Credits

Inspired by [MBW.BlueRiiot2MQTT](https://github.com/LordMike/MBW.BlueRiiot2MQTT) for the Blue Riiot
cloud API details.

## License

MIT — see [LICENSE](LICENSE).
