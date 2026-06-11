#!/usr/bin/env python3
"""
Blue Riiot Pool Data Fetcher for LoxBerry
Auth: AWS Signature V4 (Cognito Identity, region=eu-west-1, service=execute-api)
"""

import sys
import json
import hmac
import hashlib
import socket
import logging
import os
import stat
import configparser
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from cryptography.fernet import Fernet

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
PLUGIN_NAME = SCRIPT_DIR.name  # e.g. "blueconnect"

def _plugin_dir(envvar, subtree):
    """Resolve a LoxBerry plugin directory.

    Prefers the LoxBerry environment variable (set when run via the web
    frontend), otherwise derives it from the script location. On LoxBerry,
    bin/config/data/log are separate trees under /opt/loxberry, NOT siblings.
    """
    env = os.environ.get(envvar)
    if env:
        return Path(env)
    # SCRIPT_DIR = <LBROOT>/bin/plugins/<name>  ->  LBROOT is 3 levels up
    lbroot = SCRIPT_DIR.parent.parent.parent
    candidate = lbroot / subtree / "plugins" / PLUGIN_NAME
    if (lbroot / subtree).is_dir():        # LoxBerry layout detected
        return candidate
    # Local dev fallback: config/data as siblings of bin
    return SCRIPT_DIR.parent / subtree

CONFIG_DIR  = _plugin_dir("LBPCONFIGDIR", "config")
DATA_DIR    = _plugin_dir("LBPDATADIR",   "data")
CONFIG_FILE = CONFIG_DIR / "pool.cfg"
KEY_FILE    = CONFIG_DIR / ".secret_key"
LOG_FILE    = DATA_DIR   / "blueconnect.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Blue Riiot API ─────────────────────────────────────────────────────────────
API_BASE    = "https://api.riiotlabs.com/prod"
HOST        = "api.riiotlabs.com"
AWS_REGION  = "eu-west-1"
AWS_SERVICE = "execute-api"
USER_AGENT  = "okhttp/4.9.3"


# ── Passwort-Verschlüsselung ───────────────────────────────────────────────────

def _load_or_create_key() -> bytes:
    """Lädt den Fernet-Key oder erstellt ihn beim ersten Aufruf."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)   # chmod 600 — nur Owner
    log.info("New encryption key created: %s", KEY_FILE)
    return key


def encrypt_password(plaintext: str) -> str:
    f = Fernet(_load_or_create_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_password(token: str) -> str:
    f = Fernet(_load_or_create_key())
    return f.decrypt(token.encode()).decode()


def resolve_password(cfg: configparser.ConfigParser) -> tuple[str, bool]:
    """
    Gibt (Klartext-Passwort, config_changed) zurück.
    Wenn password_plain vorhanden: verschlüsseln, plain löschen → config_changed=True.
    Sonst password_enc entschlüsseln.
    """
    plain = cfg.get("blueconnect", "password_plain", fallback="").strip()
    if plain:
        enc = encrypt_password(plain)
        cfg.set("blueconnect", "password_enc",   enc)
        cfg.set("blueconnect", "password_plain", "")
        log.info("Password encrypted and stored.")
        return plain, True

    enc = cfg.get("blueconnect", "password_enc", fallback="").strip()
    if enc:
        try:
            return decrypt_password(enc), False
        except Exception:
            log.error("Password could not be decrypted - please re-enter it.")
            return "", False

    return "", False


# ── AWS SigV4 ─────────────────────────────────────────────────────────────────

def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str) -> bytes:
    k = _hmac_sha256(("AWS4" + secret).encode(), date_stamp)
    k = _hmac_sha256(k, AWS_REGION)
    k = _hmac_sha256(k, AWS_SERVICE)
    return _hmac_sha256(k, "aws4_request")


def _sigv4_headers(method: str, path: str, query: str,
                   access_key: str, secret_key: str, session_token: str) -> dict:
    now        = datetime.now(timezone.utc)
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    ph = hashlib.sha256(b"").hexdigest()

    ch = (
        f"content-type:application/json\n"
        f"host:{HOST}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n"
    )
    sh = "content-type;host;x-amz-date;x-amz-security-token"

    canonical  = f"{method}\n{path}\n{query}\n{ch}\n{sh}\n{ph}"
    cr_hash    = hashlib.sha256(canonical.encode()).hexdigest()
    cred_scope = f"{date_stamp}/{AWS_REGION}/{AWS_SERVICE}/aws4_request"
    sts        = f"AWS4-HMAC-SHA256\n{amz_date}\n{cred_scope}\n{cr_hash}"
    signature  = hmac.new(
        _signing_key(secret_key, date_stamp),
        sts.encode(), hashlib.sha256,
    ).hexdigest()

    return {
        "Content-Type":         "application/json",
        "Host":                 HOST,
        "X-Amz-Date":           amz_date,
        "X-Amz-Security-Token": session_token,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{cred_scope}, "
            f"SignedHeaders={sh}, Signature={signature}"
        ),
        "User-Agent": USER_AGENT,
    }


# ── API client ────────────────────────────────────────────────────────────────

class BlueRiiotClient:
    def __init__(self, username: str, password: str):
        self.username    = username
        self.password    = password
        self.session     = requests.Session()
        self._access_key = ""
        self._secret_key = ""
        self._sess_token = ""
        self._expires_at: datetime | None = None

    def _need_login(self) -> bool:
        if not self._expires_at:
            return True
        return datetime.now(timezone.utc) >= self._expires_at - timedelta(minutes=5)

    def login(self) -> None:
        resp = self.session.post(
            f"{API_BASE}/user/login",
            json={"email": self.username, "password": self.password},
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        creds = resp.json()["credentials"]
        self._access_key = creds["access_key"]
        self._secret_key = creds["secret_key"]
        self._sess_token = creds["session_token"]
        self._expires_at = datetime.fromisoformat(
            creds["expiration"].replace("Z", "+00:00")
        )
        log.info("Login OK - valid until %s", self._expires_at.isoformat())

    def _get(self, endpoint: str) -> dict:
        if self._need_login():
            self.login()
        path    = f"/prod/{endpoint}"
        headers = _sigv4_headers(
            "GET", path, "", self._access_key, self._secret_key, self._sess_token
        )
        resp = self.session.get(f"{API_BASE}/{endpoint}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_pools(self) -> list[dict]:
        return self._get("swimming_pool").get("data", [])

    def get_blue_devices(self, pool_id: str) -> list[dict]:
        return self._get(f"swimming_pool/{pool_id}/blue").get("data", [])

    def get_last_measurements(self, pool_id: str, blue_serial: str) -> list[dict]:
        return self._get(
            f"swimming_pool/{pool_id}/blue/{blue_serial}/lastMeasurements"
        ).get("data", [])

    def get_weather(self, pool_id: str) -> dict:
        return self._get(f"swimming_pool/{pool_id}/weather").get("data", {})


# ── Geräte-Serial-Erkennung mit Änderungs-Check ───────────────────────────────

def discover_and_verify_device(
    client: BlueRiiotClient,
    cfg: configparser.ConfigParser,
) -> tuple[str, str, str, dict, bool]:
    """
    Gibt (pool_id, pool_name, blue_serial, device, config_changed) zurück.
    Prüft bei jedem Lauf ob das gekoppelte Gerät noch stimmt.
    """
    stored_pool_id   = cfg.get("blueconnect", "pool_id",     fallback="").strip()
    stored_pool_name = cfg.get("blueconnect", "pool_name",   fallback="").strip()
    stored_serial    = cfg.get("blueconnect", "blue_serial", fallback="").strip()
    changed          = False

    # Pool-ID holen (wird gecacht, ändert sich normalerweise nicht)
    if not stored_pool_id:
        pools = client.get_pools()
        if not pools:
            raise RuntimeError("No pool found in the account.")
        pool             = pools[0]
        stored_pool_id   = pool.get("swimming_pool_id") or pool.get("id")
        stored_pool_name = pool.get("name", "Pool")
        changed          = True
        log.info("Pool detected: '%s' (%s)", stored_pool_name, stored_pool_id)

    # Blue-Gerät bei JEDEM Lauf prüfen — fängt Gerätewechsel ab
    devices = client.get_blue_devices(stored_pool_id)
    if not devices:
        raise RuntimeError("No Blue device found for the pool.")

    current_serial = devices[0].get("blue_device_serial") or devices[0].get("serial", "")

    if current_serial != stored_serial:
        if stored_serial:
            log.warning(
                "Blue device changed: %s -> %s - updating config.",
                stored_serial, current_serial,
            )
        else:
            log.info("Blue device detected: %s", current_serial)
        stored_serial = current_serial
        changed       = True

    if changed:
        cfg.set("blueconnect", "pool_id",     stored_pool_id)
        cfg.set("blueconnect", "pool_name",   stored_pool_name)
        cfg.set("blueconnect", "blue_serial", stored_serial)

    return stored_pool_id, stored_pool_name, stored_serial, devices[0], changed


# ── Werte extrahieren ─────────────────────────────────────────────────────────

def _as_number(val):
    """Return val as float if it is numeric (incl. numeric strings), else None."""
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip().replace("%", ""))
        except ValueError:
            return None
    return None


def extract_values(measurements: list[dict], weather: dict, device: dict | None = None) -> dict:
    values: dict = {}

    # Diagnostic (debug only): the raw measurements so sources can be traced.
    log.debug("Measurements (name, issuer, value): %s",
              [(m.get("name"), m.get("issuer"), m.get("value")) for m in measurements])

    # Forward every device source (sigfox over the air, blue/ble via Bluetooth
    # sync, etc.) but NOT manual test strips. When the same value exists from
    # several sources, keep the highest-priority one (live Sigfox > Bluetooth).
    issuer_priority = {"sigfox": 3, "blue": 2, "ble": 1}
    seen_priority: dict = {}
    for m in measurements:
        issuer = (m.get("issuer") or "").lower()
        if issuer == "strip":
            continue  # manual test-strip entry, not a live device reading
        name = (m.get("name") or "").lower()
        val  = m.get("value")
        if not name or val is None:
            continue
        prio = issuer_priority.get(issuer, 0)
        if name in seen_priority and seen_priority[name] >= prio:
            continue
        seen_priority[name] = prio
        values[name] = round(float(val), 3)
        if m.get("ok_min") is not None:
            values[f"{name}_ok_min"] = m["ok_min"]
        if m.get("ok_max") is not None:
            values[f"{name}_ok_max"] = m["ok_max"]

    # ── Battery ──────────────────────────────────────────────────────────────
    # The Blue device object carries the battery info. Most devices (e.g. Blue
    # Connect Go) only expose a boolean "battery_low" flag, not a percentage.
    # We therefore:
    #   1. look for a numeric battery value (measurement or device field), and
    #   2. always expose battery_low as 0/1 when the flag is present.
    nested = {}
    if device:
        log.debug("Blue device fields: %s", sorted(device.keys()))
        nd = device.get("blue_device")
        if isinstance(nd, dict):
            log.debug("blue_device content: %s", nd)
            nested = nd

    # 1) numeric battery, if the device happens to report one
    if "battery" not in values:
        for m in measurements:
            if "batt" in (m.get("name") or "").lower():
                num = _as_number(m.get("value"))
                if num is not None:
                    values["battery"] = round(num, 3)
                    break
    if "battery" not in values and device:
        for k, v in {**device, **nested}.items():
            if "bat" in k.lower():
                num = _as_number(v)
                if num is not None:
                    values["battery"] = round(num, 3)
                    log.info("Battery level from device field '%s'", k)
                    break

    # 2) low-battery flag as 0/1 (0 = OK, 1 = low / replace soon)
    flag_source = nested if "battery_low" in nested else (device or {})
    if "battery_low" in flag_source:
        values["battery_low"] = 1 if flag_source["battery_low"] else 0

    if weather:
        for key in ("temperature_current", "temperature_min", "temperature_max",
                    "uv_index", "wind_speed_current"):
            if key in weather:
                values[key] = weather[key]

    now = datetime.now(timezone.utc)
    values["last_update"]       = now.isoformat()
    values["last_update_epoch"] = int(now.timestamp())
    return values


# ── Miniserver lookup (from LoxBerry system settings) ───────────────────────────

def get_miniserver_ip(msnr: str = "") -> tuple[str, str]:
    """Read the Miniserver IP from LoxBerry's general.json.

    Returns (ip, name). msnr selects the Miniserver number ("1", "2", ...);
    empty -> first one found. The UDP target port is NOT stored here (it is the
    user-defined Loxone Virtual UDP Input port) and is configured separately.
    """
    lbsconfig = os.environ.get("LBSCONFIG")
    if lbsconfig:
        general = Path(lbsconfig) / "general.json"
    else:
        lbroot  = SCRIPT_DIR.parent.parent.parent
        general = lbroot / "config" / "system" / "general.json"

    if not general.is_file():
        log.error("general.json not found: %s", general)
        return "", ""

    try:
        data = json.loads(general.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Could not parse general.json: %s", exc)
        return "", ""

    servers = data.get("Miniserver") or {}
    if not servers:
        log.error("No Miniserver configured in LoxBerry system settings.")
        return "", ""

    key = msnr if msnr and msnr in servers else sorted(servers.keys())[0]
    entry = servers[key]
    return entry.get("Ipaddress", "").strip(), entry.get("Name", "").strip()


# ── UDP → Loxone ───────────────────────────────────────────────────────────────

def send_udp(values: dict, host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for key, val in values.items():
            if key in ("last_update", "last_update_epoch"):
                continue
            sock.sendto(f"{key}={val}\n".encode(), (host, port))
    finally:
        sock.close()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_FILE):
        log.error("Config not found: %s", CONFIG_FILE)
        return 1

    username = cfg.get("blueconnect", "username", fallback="").strip()
    password, pw_changed = resolve_password(cfg)

    if not username or not password:
        log.error("Username/password not configured.")
        return 1

    # Miniserver IP comes from LoxBerry system settings, not the plugin config.
    ms_nr   = cfg.get("loxone", "miniserver_nr",   fallback="").strip()
    ms_port = cfg.getint("loxone", "miniserver_port", fallback=7777)
    ms_ip, ms_name = get_miniserver_ip(ms_nr)
    cache   = Path(cfg.get("data", "cache_file",   fallback="/tmp/blueconnect_pool.json"))

    client = BlueRiiotClient(username, password)
    try:
        pool_id, pool_name, blue_serial, device, dev_changed = discover_and_verify_device(
            client, cfg
        )

        # Config speichern wenn Passwort verschlüsselt oder Gerät gewechselt
        if pw_changed or dev_changed:
            with open(CONFIG_FILE, "w") as fh:
                cfg.write(fh)

        log.info("Pool: '%s' | Blue: %s", pool_name, blue_serial)

        measurements = client.get_last_measurements(pool_id, blue_serial)
        weather      = client.get_weather(pool_id)
        values       = extract_values(measurements, weather, device)

        cache_data = {
            "pool":        pool_name,
            "pool_id":     pool_id,
            "blue_serial": blue_serial,
            "values":      values,
        }

        printable = {k: v for k, v in values.items()
                     if k not in ("last_update", "last_update_epoch")}
        log.info("Values: %s", printable)
        print(json.dumps(cache_data, indent=2, ensure_ascii=False))

        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(cache_data, indent=2))

        if ms_ip:
            send_udp(values, ms_ip, ms_port)
            log.info("Sent %d values -> Miniserver '%s' %s:%d",
                     len(printable), ms_name, ms_ip, ms_port)
        else:
            log.warning("No Miniserver IP available - UDP send skipped.")

    except requests.HTTPError as exc:
        log.error("HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        return 1
    except Exception as exc:
        log.exception("Error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
