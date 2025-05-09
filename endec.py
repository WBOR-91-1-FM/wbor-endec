"""
OpenENDEC V3
Decode NewsFeed EAS messages from a Sage Digital ENDEC and forward them
to Discord, GroupMe or generic webhook URLs.

The executable reads:
- a public config file (JSON) passed via --config
- a secrets file provided by systemd LoadCredential (or a fallback path)

Authors:
    - Evan Vander Stoep <@evanvs>
    - Mason Daugherty <@mdrxy>

Version: 3.0.0
Last Modified: 2025-05-09

Changelog:
    - 1.0.0 (????): Initial release <@evanvs>
    - 2.0.0 (2021-02-22): Second release <@evanvs>
    - 2.1.0 (2024-08-08): Refactored for better readability and added
        support for GroupMe <@mdrxy>
    - 2.1.2 (2025-05-08): Refactor
    - 3.0.0 (2025-05-09): Secure refactor
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import stat
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
from serial import Serial
from serial.serialutil import SerialException

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("openendec")


def _lazy_setup_logging(debug: bool, logfile: str | None) -> None:
    """
    Configure root logger.
    """

    handlers: List[logging.Handler] = []
    fmt = "% (asctime)s - %(levelname)s - %(message)s"

    if logfile:
        try:
            file_handler = logging.FileHandler(logfile, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(fmt))
            handlers.append(file_handler)
        except OSError:
            # Fall back to stderr only
            pass

    handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO, handlers=handlers, format=fmt
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    """
    Load JSON file and return the contents as a dictionary.

    Parameters:
    - path (Path): The path to the JSON file.

    Returns:
    - Dict[str, Any]: The contents of the JSON file as a dictionary.
    """
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _validate_serial_port(path: str) -> str:
    """
    Validate that the given path is a valid serial port.

    Parameters:
    - path (str): The path to the serial port.

    Raises:
    - argparse.ArgumentTypeError: If the path does not exist or is not a
        character device.

    Returns:
    - str: The validated serial port path.
    """
    if not Path(path).exists():
        raise argparse.ArgumentTypeError(f"Serial port `{path}` not found")
    if not stat.S_ISCHR(os.stat(path).st_mode):
        raise argparse.ArgumentTypeError(
            f"`{path}` exists but is not a character device"
        )
    return path


def _validate_url(u: str) -> str:
    """
    Validate that the given string is a valid URL.

    Parameters:
    - u (str): The URL to validate.

    Raises:
    - argparse.ArgumentTypeError: If the URL is invalid.

    Returns:
    - str: The validated URL.
    """
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise argparse.ArgumentTypeError(f"Invalid URL: {u!r}")
    return u


class Settings:  # pylint: disable=too-few-public-methods
    """
    Runtime configuration merged from config + secrets.
    """

    def __init__(self, public_cfg: Dict[str, Any], secrets: Dict[str, Any]):
        """
        Initialize the Settings object with public and secret
        configurations.

        Parameters:
        - public_cfg (Dict[str, Any]): The public configuration
            dictionary.
        - secrets (Dict[str, Any]): The secret configuration dictionary.

        Raises:
        - RuntimeError: If no destinations are configured.
        """
        port = public_cfg.get("port", "/dev/ttyUSB0")
        self.port = _validate_serial_port(port)

        self.debug: bool = bool(public_cfg.get("debug", False))
        self.logfile: str | None = public_cfg.get("logfile")

        raw_webhooks = secrets.get("webhooks", [])
        self.webhooks = [_validate_url(u) for u in raw_webhooks]

        raw_discord = secrets.get("discord_urls", [])
        self.discord_urls = [_validate_url(u) for u in raw_discord]

        self.groupme_bot_ids: List[str] = secrets.get("groupme_bot_ids", [])

        if not (self.webhooks or self.groupme_bot_ids or self.discord_urls):
            raise RuntimeError("No destinations configured - aborting")


# ---------------------------------------------------------------------------
# EAS helpers
# ---------------------------------------------------------------------------

EAS_EVENT_NAMES = {
    # Administrative & Test Events
    "ADR": "Administrative Message",
    "DMO": "Practice/Demo Warning",
    "NPT": "Nationwide Test of the Emergency Alert System",
    "NAT": "National Audible Test",
    "NIC": "National Information Center",
    "NMN": "Network Notification Message",
    "NST": "National Silent Test",
    "RWT": "Required Weekly Test",
    "RMT": "Required Monthly Test",
    "EAN": "Emergency Action Notification",
    # Weather-Related Events
    "BZW": "Blizzard Warning",
    "CFA": "Coastal Flood Watch",
    "CFW": "Coastal Flood Warning",
    "DSW": "Dust Storm Warning",
    "EWW": "Extreme Wind Warning",
    "FFA": "Flash Flood Watch",
    "FFW": "Flash Flood Warning",
    "FFS": "Flash Flood Statement",
    "FLA": "Flood Watch",
    "FLW": "Flood Warning",
    "FLS": "Flood Statement",
    "HWA": "High Wind Watch",
    "HWW": "High Wind Warning",
    "HUA": "Hurricane Watch",
    "HUW": "Hurricane Warning",
    "HLS": "Hurricane Local Statement",
    "SVA": "Severe Thunderstorm Watch",
    "SVR": "Severe Thunderstorm Warning",
    "SVS": "Severe Weather Statement",
    "SQW": "Snow Squall Warning",
    "SMW": "Special Marine Warning",
    "SPS": "Special Weather Statement",
    "SSA": "Storm Surge Watch",
    "SSW": "Storm Surge Warning",
    "TOA": "Tornado Watch",
    "TOR": "Tornado Warning",
    "TRA": "Tropical Storm Watch",
    "TRW": "Tropical Storm Warning",
    "TSA": "Tsunami Watch",
    "TSW": "Tsunami Warning",
    "WSA": "Winter Storm Watch",
    "WSW": "Winter Storm Warning",
    # Non-Weather Emergencies
    "AVA": "Avalanche Watch",
    "AVW": "Avalanche Warning",
    "BLU": "Blue Alert",
    "CAE": "Child Abduction Emergency",
    "CDW": "Civil Danger Warning",
    "CEM": "Civil Emergency Message",
    "EQW": "Earthquake Warning",
    "EVI": "Evacuation Immediate",
    "FRW": "Fire Warning",
    "HMW": "Hazardous Materials Warning",
    "LEW": "Law Enforcement Warning",
    "LAE": "Local Area Emergency",
    "TOE": "911 Telephone Outage Emergency",
    "NUW": "Nuclear Power Plant Warning",
    "RHW": "Radiological Hazard Warning",
    "SPW": "Shelter in Place Warning",
    "VOW": "Volcano Warning",
    "MEP": "Missing & Endangered Persons",
    # Internal-Only Codes
    "TXB": "Transmitter Backup On",
    "TXF": "Transmitter Carrier Off",
    "TXO": "Transmitter Carrier On",
    "TXP": "Transmitter Primary On",
    # Future Implementation Codes
    "BHW": "Biological Hazard Warning",
    "BWW": "Boil Water Warning",
    "CHW": "Chemical Hazard Warning",
    "CWW": "Contaminated Water Warning",
    "DBA": "Dam Watch",
    "DBW": "Dam Break Warning",
    "DEW": "Contagious Disease Warning",
    "EVA": "Evacuation Watch",
    "FCW": "Food Contamination Warning",
    "IBW": "Iceberg Warning",
    "IFW": "Industrial Fire Warning",
    "LSW": "Landslide Warning",
    "POS": "Power Outage Advisory",
    "WFA": "Wild Fire Watch",
    "WFW": "Wild Fire Warning",
}

HEADER_RE = re.compile(
    r"^ZCZC-"  # start
    r"(?P<org>[A-Z]{3})-"  # ORG
    r"(?P<event>[A-Z]{3})-"  # EEE
    r"(?P<locs>(?:\d{6}-){0,30}\d{6})"  # 1-31 location codes
    r"\+(?P<dur>\d{4})-"  # +TTTT
    r"(?P<ts>\d{7})-"  # JJJHHMM
    r"(?P<sender>[A-Z0-9/]{8})-"  # LLLLLLLL
    r"$"
)


def parse_eas(header: str) -> Dict[str, str]:
    """
    Parse the EAS header and return a dictionary with the parsed fields.
    The input header format is expected to be:

    ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-

    Every field is fixed-width, made of 7-bit ASCII, separated by the
    literal dash ( - ), with a single plus ( + ) introducing the
    valid-time field.

    where:
    - ZCZC: EAS header start (fixed)
    - ORG: Originator code (EAS, CIV, WXR, PEP) (A-Z, 3 chars)
    - EEE: Event code (e.g. TOR, RWT, EAN. 80+ defined) (A-Z, 3 chars)
    - PSSCCC: Location code (e.g. 12345, 123456) (0-9 chars)
    - +: Separator (fixed)
    - TTTT: Duration (Valid time in hhmm) (4 digits)
    - JJJHHMM: Issue/start time (UTC, JJJ = day-of-year 001-366,
        HHMM = 24-h time) (8 digits)
    - LLLLLLLL: ID of the sending station (8 chars)
    - -: End of header (fixed)

    This function:
    - Locks every field to spec widths
    - Handles up to 31 locations
    - Keeps trailing dash
    - Converts duration and timestamp immediately

    Parameters:
    - header (str): The EAS header string to parse.

    Returns:
    - Dict[str, str]: A dictionary containing the parsed fields:
        - org: Originator code
        - event: Event code
        - locs: List of location codes
        - duration_minutes: Duration in minutes
        - duration_raw: Raw duration string
        - start_utc: Start time in ISO UTC format
        - timestamp_raw: Raw timestamp string
        - sender: Sender ID
        - event_name: Human-readable event name (if available)
        - raw_header: The original header string
    """
    m = HEADER_RE.match(header)
    if not m:
        raise ValueError("Malformed EAS header")

    g = m.groupdict()

    # Duration to minutes
    hours, mins = divmod(int(g["dur"]), 100)
    duration_minutes = hours * 60 + mins

    # JJJHHMM to ISO UTC (current year)
    jjj, hh, mm = int(g["ts"][:3]), int(g["ts"][3:5]), int(g["ts"][5:])
    y_start = datetime.utcnow().replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    start_utc = (y_start + timedelta(days=jjj - 1, hours=hh, minutes=mm)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )

    return {
        "org": g["org"],
        "event": g["event"],
        "locs": g["locs"].split("-"),
        "duration_minutes": duration_minutes,
        "duration_raw": g["dur"],
        "start_utc": start_utc,
        "timestamp_raw": g["ts"],
        "sender": g["sender"],
        "event_name": EAS_EVENT_NAMES.get(g["event"], "Unknown"),
        "raw_header": header,
    }


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


class Webhook:  # pylint: disable=too-few-public-methods
    """
    Generic webhook POST client.

    This class is used to send POST requests to a specified webhook URL.
    """

    def __init__(self, url: str):
        """
        Initialize the Webhook object with the specified URL.

        Parameters:
        - url (str): The webhook URL to send POST requests to.
        """
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def post(self, payload: Dict[str, Any]) -> None:
        """
        Send a POST request to the webhook URL with the given payload.

        Parameters:
        - payload (Dict[str, Any]): The payload to send in the POST
            equest.

        Raises:
        - requests.RequestException: If the POST request fails.
        """
        LOGGER.info("POST to `%s`", self.url)
        try:
            resp = requests.post(
                self.url, headers=self.headers, json=payload, timeout=10
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("Webhook POST to `%s` failed: %s", self.url, exc)


class Discord:  # pylint: disable=too-few-public-methods
    """
    Discord webhook client.
    """

    def __init__(self, urls: List[str]):
        """
        Initialize the Discord object with a list of webhook URLs.

        Parameters:
        - urls (List[str]): A list of Discord webhook URLs to send
            messages to.
        """
        self.urls = urls

    def post(self, content: str, eas_fields: Dict[str, str]) -> None:
        """
        Send a message to Discord with the given content and EAS fields.

        Parameters:
        - content (str): The message content to send.
        - eas_fields (Dict[str, str]): A dictionary containing EAS
            fields to include in the message as embedded fields.
        """
        duration = eas_fields.get("duration")
        embed = {
            "title": "EAS Message",
            "description": content,
            "fields": [
                {
                    "name": "Event",
                    "value": f"{eas_fields.get('event_name')} ({eas_fields.get('event')})",
                    "inline": True,
                },
                {
                    "name": "Location",
                    "value": eas_fields.get("location", "not found"),
                    "inline": True,
                },
                {
                    "name": "Duration",
                    "value": f"{duration} min" if duration else "not found",
                    "inline": True,
                },
                {
                    "name": "Start",
                    "value": eas_fields.get("start", "not found"),
                    "inline": True,
                },
                {
                    "name": "ID",
                    "value": eas_fields.get("id", "not found"),
                    "inline": False,
                },
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        payload = {"embeds": [embed]}
        for u in self.urls:
            Webhook(u).post(payload)


class GroupMe:  # pylint: disable=too-few-public-methods
    """
    GroupMe bot client.
    """

    def __init__(self, bot_ids: List[str]):
        """
        Initialize the GroupMe object with a list of bot IDs.

        Parameters:
        - bot_ids (List[str]): A list of GroupMe bot IDs to send
            messages to.
        """
        self.bot_ids = bot_ids
        self.url = "https://api.groupme.com/v3/bots/post"

    def post(self, message: str) -> None:
        """
        Send a message to GroupMe with the given content via a Bot ID.

        Parameters:
        - message (str): The message content to send.
        """
        footer = (
            "\n\nThis message was sent using OpenENDEC V2\n"
            "[WBOR-91-1-FM/wbor-endec]\n----------"
        )
        body = f"{message}{footer}"

        # Split body into 500 character segments (max length for GroupMe messages)
        segments = [body[i : i + 500] for i in range(0, len(body), 500)]
        for segment in segments:
            for bot_id in self.bot_ids:
                payload = {"bot_id": bot_id, "text": segment}
                Webhook(self.url).post(payload)


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------


def dispatch(msg: str, eas_fields: Dict[str, str], cfg: Settings) -> None:
    """
    Dispatch the message to the configured destinations.

    Parameters:
    - msg (str): The message content to send.
    - eas_fields (Dict[str, str]): A dictionary containing EAS fields to
        include in the message.
    - cfg (Settings): The runtime configuration object containing
        destination information.
    """
    payload = {"message": msg, "eas": eas_fields} if eas_fields else {"message": msg}
    for url in cfg.webhooks:
        Webhook(url).post(payload)

    # Multi-destination is handled by the Discord and GroupMe classes
    # so we don't need to loop through them here.
    if cfg.discord_urls:
        Discord(cfg.discord_urls).post(msg, eas_fields)

    if cfg.groupme_bot_ids:
        GroupMe(cfg.groupme_bot_ids).post(msg)


# ---------------------------------------------------------------------------
# Serial processing loop
# ---------------------------------------------------------------------------


def process_serial(cfg: Settings) -> None:
    """
    Main event loop for processing serial input.
    This function continuously reads from the serial port and processes
    incoming News Feed messages.

    Parameters:
    - cfg (Settings): The runtime configuration object containing serial
        port information.

    Raises:
    - SerialException: If there is an error with the serial port.
    - requests.RequestException: If there is an error with the webhook
    """

    def transform_and_send(lines: List[str]) -> None:
        """
        Transform the incoming lines into a message and send it to the
        configured destinations.

        Parameters:
        - lines (List[str]): The list of lines read from the serial
            port.
        """
        eas_fields: Dict[str, str] = {}

        # Strip final header line
        if lines and lines[-1].startswith("ZCZC"):
            eas_fields = parse_eas(lines.pop())

        message = " ".join(lines)
        dispatch(message, eas_fields, cfg)

    while True:
        ser = None
        try:
            ser = Serial(cfg.port, baudrate=9600, bytesize=8, stopbits=1)
            LOGGER.debug("Serial port `%s` opened", cfg.port)

            while ser.isOpen():
                LOGGER.debug("Entering read loop on `%s`", cfg.port)

                raw = ser.readline()
                LOGGER.debug("Raw input: %r", raw)
                line = raw.decode("utf-8", errors="ignore")

                if "<ENDECSTART>" in line:
                    LOGGER.debug("Found <ENDECSTART> in line: %r", line)

                if "<ENDECSTART>" not in line:
                    continue

                # Read until <ENDECEND> is found
                buffer: List[str] = []
                for raw2 in iter(ser.readline, b""):
                    chunk = raw2.decode("utf-8", errors="ignore")
                    if "<ENDECEND>" in chunk:
                        break
                    buffer.append(chunk.strip())

                # Process the buffer
                if buffer:
                    LOGGER.debug(
                        "Collected %d lines for one EAS payload: %s",
                        len(buffer),
                        buffer,
                    )
                    transform_and_send(buffer)
        except (SerialException, requests.RequestException) as exc:
            LOGGER.warning("Serial loop error: %s", exc)
        finally:
            if ser and ser.isOpen():
                ser.close()
                LOGGER.debug("Serial port `%s` closed", cfg.port)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:  # pylint: disable=missing-function-docstring
    # Get public config
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", required=True, type=Path, help="Path to public config JSON"
    )
    args = parser.parse_args()
    public_cfg = _load_json(args.config)

    # Get secrets
    secret_path_str = os.getenv("SECRETS_PATH", "/etc/openendec/secrets.json")
    secret_path = Path(secret_path_str)
    secrets = _load_json(secret_path)

    # Merge configs
    cfg = Settings(public_cfg, secrets)

    _lazy_setup_logging(cfg.debug, cfg.logfile)

    LOGGER.info("OpenENDEC V3 starting - serial on `%s`", cfg.port)
    LOGGER.info(
        "Originally Written By: Evan Vander Stoep [https://github.com/EvanVS]\n"
        "Modified by: Mason Daugherty [@mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\n"
        "Logger Started!\n"
    )

    # Launch main loop
    process_serial(cfg)


if __name__ == "__main__":
    main()
