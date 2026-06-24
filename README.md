# BlueConnect — LoxBerry Plugin

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
2. For each value add a **Virtual UDP Input Command**. Set its *command
   recognition* and, optionally, the *unit* (Einheit) for a nicely formatted
   display in Loxone (`\v` is the value placeholder):

   | Value                 | Command recognition          | Unit (Einheit) |
   |-----------------------|------------------------------|----------------|
   | Water temperature     | `temperature=\v`             | `<v.1>°C`      |
   | pH                    | `ph=\v`                      | `<v.1>`        |
   | ORP (Redox)           | `orp=\v`                     | `<v> mV`       |
   | Battery low flag      | `battery_low=\v`             | `<v>`          |
   | Last measurement      | `measurement_loxone=\v`      | `<v.u>`        |
   | Last measurement (time only) | `measurement_time_of_day=\v` | `<v.t>` |
   | Air temperature       | `temperature_current=\v`     | `<v.1>°C`      |

   Only values your device actually reports are sent. The Blue Connect device
   measures **pH, water temperature and ORP (Redox)** — it does **not** measure
   free chlorine or TDS. (ORP/Redox reflects the disinfection power; there is no
   direct mg/l chlorine reading on this device.) Values like free chlorine only
   exist if you enter **test strips** in the Blue Riiot app, and those manual
   strip entries are intentionally filtered out — only live device readings are
   forwarded.

   The battery is exposed only as a low-battery flag — `battery_low`: `0` =
   **OK**, `1` = **Low** — not as a percentage. Tip: in Loxone you can map the
   `battery_low` input to a status text "OK"/"Low" via its unit/caption.

   **`measurement_loxone`** is the time of the last actual sensor measurement
   (what the Blue Riiot app shows as "last updated"), already in Loxone time
   (seconds since 2009-01-01) computed in the **LoxBerry system timezone**
   (incl. DST). Map `measurement_loxone=\v` and set the input's **unit (Einheit)**
   to `<v.u>`; Loxone then shows the real local date/time directly — no
   calculation needed. (Make sure your LoxBerry timezone is set correctly —
   `timedatectl`.) For the **time only**, use `measurement_time_of_day` (seconds
   since local midnight) with unit `<v.t>` → shows e.g. `21:33:00`.

## Configuration

| Setting          | Description                                                            |
|------------------|------------------------------------------------------------------------|
| Email / Password | Your Blue Riiot account credentials.                                   |
| UDP port         | Port of your Loxone Virtual UDP Input (default `7777`).                |
| Polling interval | Seconds between fetches. The Blue device sends roughly every 72 min.   |

The Miniserver IP is taken from LoxBerry's system settings (**Settings → Miniserver**) — no manual
entry needed.

## Automatic updates

The plugin supports LoxBerry's built-in auto-update. In `plugin.cfg` the `[AUTOUPDATE]` section
points `RELEASECFG` to [`release.cfg`](release.cfg) in this repository. LoxBerry periodically reads
that file and, when its `VERSION` is newer than the installed one, downloads the archive from
`ARCHIVEURL` and installs it automatically.

Enable it in LoxBerry under the plugin's update settings (or it runs automatically because
`AUTOMATIC_UPDATES=1`).

### Cutting a new release

1. Bump `VERSION` in **both** `plugin.cfg` (`[PLUGIN] VERSION`) and `release.cfg`.
2. Point `ARCHIVEURL` in `release.cfg` at the new tag archive
   (`.../archive/refs/tags/vX.Y.Z.zip`).
3. Commit and push to `main`.
4. Create and push a matching git tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
   GitHub serves the tag's source archive automatically — that is what LoxBerry downloads.
5. (Optional) Draft a GitHub Release from the tag for human-readable notes.

## Building a plugin ZIP

The archive must use forward-slash paths and have `plugin.cfg` at the root. On Windows
(PowerShell), build it with .NET to guarantee correct separators:

```powershell
$proj = "."
$out  = "blueconnect.zip"
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

On Linux/macOS a plain `zip -r blueconnect.zip . -x '*.git*' 'data/*' '*.zip'` works.

## Project layout

```
plugin.cfg                       LoxBerry plugin metadata
dpkg/apt                         apt packages installed on setup
cron/cron.05min/blueconnect        polling job (honours configured interval)
bin/fetch_pool.py                main fetcher (API, encryption, UDP)
webfrontend/htmlauth/index.cgi   tabbed web UI (creates the config on first save)
icons/icon_*.png                 plugin icons
```

## Credits

Inspired by [MBW.BlueRiiot2MQTT](https://github.com/LordMike/MBW.BlueRiiot2MQTT) for the Blue Riiot
cloud API details.

## License

MIT — see [LICENSE](LICENSE).
