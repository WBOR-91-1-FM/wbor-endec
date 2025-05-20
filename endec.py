"""
wbor-endec
Decode NewsFeed EAS messages from a Sage Digital ENDEC and forward them
to Discord, GroupMe or generic webhook URLs.

The executable reads:
- a public config file (JSON) passed via --config
- a secrets file provided by systemd LoadCredential (or a fallback path)

Authors:
- Evan Vander Stoep <@evanvs>
- Mason Daugherty <@mdrxy>

Version: 4.1.1
Last Modified: 2025-05-12

Changelog:
    - 1.0.0 (????): Initial release <@evanvs>
    - 2.0.0 (2021-02-22): Second release <@evanvs>
    - 2.1.0 (2024-08-08): Refactored for better readability and added
        support for GroupMe <@mdrxy>
    - 2.1.2 (2025-05-08): Refactor
    - 3.0.0 (2025-05-09): Secure refactor
    - 4.0.0 (2025-05-10): Added RabbitMQ support and some refactors
    - 4.1.0 (2025-05-11): Enhanced EAS parsing with human readable
        sender names, logging improvements, Python 3.7 backward
        compatibility, timezone fixes, guard against some config values,
        adjust log levels of some dependencies,
    - 4.1.1 (2025-05-12): Make RabbitMQ routing key configurable in
        secrets file
"""  # pylint: disable=too-many-lines

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Ensures compatibility with both Python 3.7 (via backports.zoneinfo) and newer
# versions
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[import]

import pika
import requests
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPChannelError, AMQPConnectionError, UnroutableError
from serial import Serial
from serial.serialutil import SerialException

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("wbor-endec")


def _lazy_setup_logging(debug: bool, logfile: str | None) -> None:
    """
    Configure root logger.
    """
    handlers: List[logging.Handler] = []
    fmt = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

    if logfile:
        try:
            file_handler = logging.FileHandler(logfile, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(fmt))
            handlers.append(file_handler)
        except OSError:
            LOGGER.warning("Could not open logfile %s, falling back to stderr", logfile)

    # Stream handler for console output
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(fmt))
    handlers.append(stream_handler)

    # Remove existing handlers to avoid duplication if called multiple times
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure the root logger with stream and file handlers
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in handlers:
        root_logger.addHandler(handler)

    logging.getLogger("pika").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# RabbitMQ Publisher
# ---------------------------------------------------------------------------


class RabbitMQPublisher:
    """
    A RabbitMQ publisher class that handles connection, channel
    management, exchange declaration, and message publishing with
    retries and publisher confirms.
    """

    def __init__(self, amqp_url: str, exchange_name: str, exchange_type: str = "topic"):
        self.amqp_url = amqp_url
        self.exchange_name = exchange_name
        self.exchange_type = exchange_type
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[BlockingChannel] = None
        self.logger = logging.getLogger(__name__ + ".RabbitMQPublisher")
        self._connect()
        self._connect()

    def _connect(self) -> None:
        """
        Handle connection to RabbitMQ server and channel declaration.
        """
        if self._connection and self._connection.is_open:
            # Already connected
            return
        try:
            self.logger.debug(
                "Attempting to connect to RabbitMQ server at %s",
                self.amqp_url.split("@")[-1],
            )
            self._connection = pika.BlockingConnection(
                pika.URLParameters(self.amqp_url)
            )
            self._channel = self._connection.channel()
            self._channel.exchange_declare(
                exchange=self.exchange_name,
                exchange_type=self.exchange_type,
                durable=True,
            )

            # Enable publisher confirms, which allows us to confirm that
            # messages have been successfully published to the exchange
            # and not just simply sent to the RabbitMQ server.
            self._channel.confirm_delivery()
            self.logger.info(
                "Successfully connected to RabbitMQ and ensured exchange `%s` "
                "(type: %s)",
                self.exchange_name,
                self.exchange_type,
            )
        except AMQPConnectionError as e:
            self.logger.critical("Failed to connect to RabbitMQ: %s", e)
            self._connection = None
            self._channel = None
            raise

    def _ensure_connected(self) -> None:
        """
        Check if the connection and channel are open. If not, attempt to
        reconnect.
        """
        if (
            not self._connection
            or self._connection.is_closed
            or not self._channel
            or self._channel.is_closed
        ):
            self.logger.warning(
                "RabbitMQ connection/channel is closed or not established. Reconnecting..."
            )
            self._connect()

    def ensure_connection(self) -> None:
        """
        Public method to ensure RabbitMQ connection is active.
        """
        self._ensure_connected()

    def publish(
        self,
        message_body: Dict[str, Any],
        routing_key: str,
        retry_attempts: int = 3,
        retry_delay_seconds: int = 5,
    ) -> bool:
        """
        Publish a message to the RabbitMQ exchange with the specified
        routing key.

        Parameters:
        - message_body (Dict[str, Any]): The message body to publish.
        - routing_key (str): The routing key to use for the message.
        - retry_attempts (int): Number of retry attempts on failure.
        - retry_delay_seconds (int): Delay between retry attempts in
            seconds.

        Returns:
        - bool: True if the message was published successfully, False
            otherwise.
        """
        self._ensure_connected()
        if (
            not self._channel
        ):  # Should not happen if _ensure_connected works, but as a safeguard
            self.logger.error("Cannot publish, channel is not available.")
            return False

        message_body_str = json.dumps(message_body)

        for attempt in range(retry_attempts):
            try:
                if self._channel.basic_publish(
                    exchange=self.exchange_name,
                    routing_key=routing_key,
                    body=message_body_str,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # 2 is persistent delivery mode
                        content_type="application/json",
                    ),
                    mandatory=True,  # Important for unroutable messages
                ):
                    self.logger.info(
                        "Successfully published and confirmed message to "
                        "exchange `%s` with routing key `%s`",
                        self.exchange_name,
                        routing_key,
                    )
                    return True
                self.logger.warning(
                    "Message to exchange `%s` with routing key `%s` was "
                    "NACKed or not confirmed (attempt %d/%d).",
                    self.exchange_name,
                    routing_key,
                    attempt + 1,
                    retry_attempts,
                )
                # Handle NACK: could retry, log, or send to DLX.
            except UnroutableError:
                self.logger.error(
                    "Message to exchange `%s` with routing key `%s` was unroutable. "
                    "Ensure a queue is bound with this routing key or the exchange exists "
                    "correctly.",
                    self.exchange_name,
                    routing_key,
                )
                return False  # Do not retry unroutable messages automatically
            except (
                AMQPConnectionError,
                AMQPChannelError,
            ) as e:
                self.logger.error(
                    "Connection/Channel error during publish (attempt %d/%d): %s",
                    attempt + 1,
                    retry_attempts,
                    e,
                )
                if attempt < retry_attempts - 1:
                    time.sleep(
                        retry_delay_seconds * (attempt + 1)
                    )  # Exponential backoff might be better
                    self.logger.info(
                        "Retrying publish in %d seconds...", retry_delay_seconds
                    )
                    self._connect()  # Attempt to reconnect
                else:
                    self.logger.error(
                        "Failed to publish message after %d attempts.", retry_attempts
                    )
                    return False  # Indicate failure
            except Exception as e:  # pylint: disable=broad-except
                self.logger.error(
                    "An unexpected error occurred during publish (attempt %d/%d): %s",
                    attempt + 1,
                    retry_attempts,
                    e,
                )
                # Fall through to retry or fail after attempts

            if attempt < retry_attempts - 1:
                self.logger.info(
                    "Retrying publish in %d seconds...", retry_delay_seconds
                )
                time.sleep(retry_delay_seconds)
            else:
                self.logger.error(
                    "Failed to publish message to exchange `%s` with routing key `%s` after %d "
                    "attempts.",
                    self.exchange_name,
                    routing_key,
                    retry_attempts,
                )
                return False
        return False

    def close(self) -> None:
        """
        Close the RabbitMQ connection and channel.
        """
        try:
            if self._channel and self._channel.is_open:
                self._channel.close()
                self.logger.info("RabbitMQ channel closed.")
        except Exception as e:  # pylint: disable=broad-except
            self.logger.error("Error closing RabbitMQ channel: %s", e)
        try:
            if self._connection and self._connection.is_open:
                self._connection.close()
                self.logger.info("RabbitMQ connection closed.")
        except Exception as e:  # pylint: disable=broad-except
            self.logger.error("Error closing RabbitMQ connection: %s", e)
        self._channel = None
        self._connection = None


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
        # Allow AMQP/AMQPS for RabbitMQ
        if p.scheme not in ("amqp", "amqps"):
            raise argparse.ArgumentTypeError(f"Invalid URL: `{u!r}`")
    return u


class Settings:  # pylint: disable=too-few-public-methods, too-many-instance-attributes
    """
    Runtime configuration merged from config + secrets.
    """

    def __init__(self, public_cfg: Dict[str, Any], secrets: Dict[str, Any]):
        """
        Initialize the Settings object with public and secret
        configurations.

        Defaults to `/dev/ttyUSB0` for the serial port and `False` for
        debug mode if not specified.

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

        self.rabbitmq_amqp_url: Optional[str] = secrets.get("rabbitmq_amqp_url")
        if self.rabbitmq_amqp_url:
            _validate_url(self.rabbitmq_amqp_url)
        self.rabbitmq_exchange_name: Optional[str] = secrets.get(
            "rabbitmq_exchange_name"
        )

        # Default routing key if not specified in secrets
        self.rabbitmq_routing_key: str = secrets.get(
            "rabbitmq_routing_key", "notification.wbor-endec"
        )

        if self.rabbitmq_amqp_url and not self.rabbitmq_exchange_name:
            raise RuntimeError(
                "RabbitMQ AMQP URL provided but exchange name is missing."
            )

        if not (
            self.webhooks
            or self.groupme_bot_ids
            or self.discord_urls
            or self.rabbitmq_amqp_url
        ):
            raise RuntimeError(
                "No destinations configured (webhooks, groupme, discord, or "
                "rabbitmq) - aborting"
            )


# ---------------------------------------------------------------------------
# Location lookup
# ---------------------------------------------------------------------------


def _load_location_map() -> tuple[dict[str, str], dict[str, str]]:
    """
    Load information from the national_county.txt file and return two
    dictionaries:
    - loc_map: Maps PSSCCC location codes to human-readable names.
    - state_map: Maps state FIPS codes to their corresponding
        abbreviations.
    """
    loc_map: dict[str, str] = {}
    state_map: dict[str, str] = {}

    # Assumes `national_county.tx`t in the same directory as the script
    fn = Path(__file__).parent / "national_county.txt"
    if not fn.exists():
        LOGGER.warning(
            "Location map file not found: %s. Location lookups will fail.",
            fn,
        )
        return loc_map, state_map

    with fn.open(encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(",")

            # Expect `ABBR,state_fips,county_fips,county_name,class`
            if len(parts) < 4:
                # Malformed line, skip
                continue

            abbr, st, co, county_name = parts[:4]

            # Zero-fill state and county codes
            st, co = st.zfill(2), co.zfill(3)

            key = st + co
            loc_map[key] = f"{county_name}, {abbr}"

            # If its the first time we see this state_fips, record abbr
            state_map.setdefault(st, abbr)
    return loc_map, state_map


_LOC_MAP, _STATE_MAP = _load_location_map()


def _lookup_location(code: str) -> str:
    """
    Return a human-readable location name for the given PSSCCC code.

    Parameters:
    - code (str): The 6-digit PSSCCC string, e.g. "023005".

    Returns:
    - str: The human-readable location name, or "Unknown" if not found.
    """
    if not code or len(code) != 6:
        return "Invalid Code"
    ssccc = code[1:]  # Drop leading placeholder
    st, co = ssccc[:2], ssccc[2:]
    if co == "000":
        return _STATE_MAP.get(st, "Unknown State")

    return _LOC_MAP.get(ssccc, "Unknown County/Area")


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

# Define sets of event codes by category
ADMIN_CODES = {"ADR", "DMO", "NPT", "NAT", "NIC", "NMN", "NST", "RWT", "RMT", "EAN"}
WEATHER_CODES = {
    "BZW",
    "CFA",
    "CFW",
    "DSW",
    "EWW",
    "FFA",
    "FFW",
    "FFS",
    "FLA",
    "FLW",
    "FLS",
    "HWA",
    "HWW",
    "HUA",
    "HUW",
    "HLS",
    "SVA",
    "SVR",
    "SVS",
    "SQW",
    "SMW",
    "SPS",
    "SSA",
    "SSW",
    "TOA",
    "TOR",
    "TRA",
    "TRW",
    "TSA",
    "TSW",
    "WSA",
    "WSW",
}
NONWEATHER_CODES = {
    "AVA",
    "AVW",
    "BLU",
    "CAE",
    "CDW",
    "CEM",
    "EQW",
    "EVI",
    "FRW",
    "HMW",
    "LEW",
    "LAE",
    "TOE",
    "NUW",
    "RHW",
    "SPW",
    "VOW",
    "MEP",
}
INTERNAL_CODES = {"TXB", "TXF", "TXO", "TXP"}
FUTURE_CODES = {
    "BHW",
    "BWW",
    "CHW",
    "CWW",
    "DBA",
    "DBW",
    "DEW",
    "EVA",
    "FCW",
    "IBW",
    "IFW",
    "LSW",
    "POS",
    "WFA",
    "WFW",
}

CATEGORY_COLORS = {
    "administrative": 0x3498DB,  # blue
    "weather": 0xF1C40F,  # yellow
    "emergency": 0xE74C3C,  # red
    "internal": 0x95A5A6,  # grey
    "future": 0xE74C3C,  # red
}

# EAS header regex, spec defined in parse_eas() docstring
# HEADER_RE (strict, anchored)
# HEADER_SEARCH_RE (not anchored)
HEADER_RE = re.compile(
    r"^ZCZC-"  # Start
    r"(?P<org>[A-Z]{3})-"
    r"(?P<event>[A-Z]{3})-"  # EEE
    r"(?P<locs>(?:\d{6}-){0,30}\d{6})"  # 1-31 location codes
    r"\+(?P<dur>\d{4})-"  # +TTTT
    r"(?P<ts>\d{7})-"  # JJJHHMM
    r"(?P<sender>[A-Za-z0-9/ ]{8})-$"  # LLLLLLLL-
)

HEADER_SEARCH_RE = re.compile(
    r"ZCZC-"  # Start
    r"(?P<org>[A-Z]{3})-"
    r"(?P<event>[A-Z]{3})-"  # EEE
    r"(?P<locs>(?:\d{6}-){0,30}\d{6})"  # 1-31 location codes
    r"\+(?P<dur>\d{4})-"  # +TTTT
    r"(?P<ts>\d{7})-"  # JJJHHMM
    r"(?P<sender>[A-Za-z0-9/ ]{8})-"  # LLLLLLLL-
)


def parse_eas(header: str) -> Dict[str, Any]:
    """
    Parse the EAS header and return a dictionary with the parsed fields.
    The input header format is expected to be:

    ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-

    Every field is fixed-width, made of 7-bit ASCII, separated by the
    literal dash (`-`), with a single plus (`+`) introducing the
    valid-time field.

    Components:
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
    - Dict[str, Any]: A dictionary containing the parsed fields:
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
    m = HEADER_SEARCH_RE.match(header)
    if not m:
        raise ValueError("Malformed EAS header")

    g = m.groupdict()

    # Duration to minutes
    hours, mins = divmod(int(g["dur"]), 100)
    duration_minutes = hours * 60 + mins

    # Clean up space-padded sender
    sender = g["sender"].rstrip()

    # Get a human readable sender name equivalent
    org_human = {
        "EAS": "Emergency Alert System",
        "CIV": "Civil Authorities",
        "WXR": "National Weather Service",
        "PEP": "Primary Entry Point (National)",
    }.get(g["org"], g["org"])

    # JJJHHMM to ISO UTC (current year)
    jjj, hh, mm = int(g["ts"][:3]), int(g["ts"][3:5]), int(g["ts"][5:])

    now_utc_aware = datetime.now(timezone.utc)
    year = now_utc_aware.year

    y_start = datetime(year, 1, 1, tzinfo=timezone.utc)
    start_utc = (y_start + timedelta(days=jjj - 1, hours=hh, minutes=mm)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )

    timestamp_et = (
        (
            datetime(year, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=jjj - 1, hours=hh, minutes=mm)
        )
        .astimezone(ZoneInfo("America/New_York"))
        .isoformat(timespec="minutes")
    )

    # Get human-readable location names, stored alongside the raw codes
    raw_locs = g["locs"].split("-")
    locs = [_lookup_location(loc) for loc in raw_locs]

    return {
        "org_raw": g["org"],
        "org": org_human,
        "event": g["event"],
        "locs": locs,  # Human-readable location names
        "raw_locs": raw_locs,  # Raw location codes
        "duration_minutes": duration_minutes,
        "duration_raw": g["dur"],
        "start_utc": start_utc,
        "timestamp_raw": g["ts"],
        "timestamp_et": timestamp_et,
        "sender": sender,
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
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "WBOR-91-1-FM/wbor-endec",
        }

    def post(self, payload: Dict[str, Any]) -> None:
        """
        Send a POST request to the webhook URL with the given payload.

        Parameters:
        - payload (Dict[str, Any]): The payload to send in the POST
            request.

        Raises:
        - requests.RequestException: If the POST request fails.
        """
        LOGGER.info("POST to `%s`", self.url)
        try:
            resp = requests.post(
                self.url, headers=self.headers, json=payload, timeout=10
            )
            resp.raise_for_status()
            LOGGER.debug(
                "Webhook POST to `%s` successful, status %s", self.url, resp.status_code
            )
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
        self.webhook_clients = [Webhook(url) for url in urls]

    def post(self, content: str, eas_fields: Dict[str, Any]) -> None:
        """
        Send a message to Discord with the given content and EAS fields.

        Parameters:
        - content (str): The message content to send.
        - eas_fields (Dict[str, Any]): A dictionary containing EAS
            fields to include in the message as embedded fields.
        """
        # Determine color based on event code
        code = eas_fields.get("event", "")
        if code in ADMIN_CODES:
            cat = "administrative"
        elif code in WEATHER_CODES:
            cat = "weather"
        elif code in NONWEATHER_CODES:
            cat = "emergency"
        elif code in INTERNAL_CODES:
            cat = "internal"
        elif code in FUTURE_CODES:
            cat = "future"
        else:
            cat = "emergency"  # Default for unknown codes
        color = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["emergency"])

        embed_fields = [
            # Event name
            {
                "name": "Event",
                "value": (
                    f"{eas_fields.get('event_name', 'Not found')} "
                    f"({eas_fields.get('event', 'Not found')})"
                ),
                "inline": True,
            },
            # Duration in minutes
            {
                "name": "Duration (min)",
                "value": str(eas_fields.get("duration_minutes", "Not found")),
                "inline": True,
            },
            # Start timestamp in UTC
            {
                "name": "Start (UTC)",
                "value": eas_fields.get("start_utc", "Not found"),
                "inline": True,
            },
            # Sending station's ID
            {
                "name": "Sender",
                "value": eas_fields.get("sender", "Not found"),
                "inline": True,
            },
            # Originator
            {
                "name": "Originator",
                "value": (
                    f'{eas_fields.get("org", "Not found")} '
                    f'({eas_fields.get("org_raw", "Not found")})'
                ),
                "inline": True,
            },
            # Timestamp Raw
            {
                "name": "Start (ET)",
                "value": eas_fields.get("timestamp_et", "Not found"),
                "inline": True,
            },
            # All location codes
            {
                "name": "Locations",
                "value": ", ".join(eas_fields.get("locs", [])) or "Not found",
                "inline": False,  # Best on its own line if long
            },
            {
                "name": "Raw Header",
                "value": f"```{eas_fields.get('raw_header', 'Not found')}```",
                "inline": False,
            },
        ]

        embed = {
            "title": f"EAS Alert: {eas_fields.get('event_name', 'Unknown Event')}",
            "description": content if content else "See details in fields.",
            "color": color,
            "fields": embed_fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Powered by WBOR-91-1-FM/wbor-endec"},
        }
        payload = {"embeds": [embed], "username": "WBOR ENDEC Alerter"}

        for client in self.webhook_clients:
            client.post(payload)


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
        self.webhook_client = Webhook(self.url)

    def post(  # pylint: disable=too-many-locals
        self, message: str, eas_fields: Dict[str, Any]
    ) -> None:
        """
        Send a message to GroupMe with the given content via a Bot ID.

        Parameters:
        - message (str): The message content to send.
        - eas_fields (Dict[str, Any]): A dictionary containing EAS
            fields to include in the message.
        """
        event_name = eas_fields.get("event_name", "Unknown Event")
        locs_str = ", ".join(eas_fields.get("locs", [])) or "Not found"
        duration = eas_fields.get("duration_minutes", "Not found")
        start_time = eas_fields.get("timestamp_et", "Not found")

        full_message = (
            f"EAS Alert: {event_name}\n\n"
            f"Message: {message}\n\n"
            f"Locations: {locs_str}\n\n"
            f"Duration: {duration} minutes\n\n"
            f"Starts: {start_time} (ET)\n\n"
            f"Sender: {eas_fields.get('sender', 'Not found')}"
        )

        footer = (
            "\n\n(This is an automated message)\n"
            "(WBOR-91-1-FM/wbor-endec)\n----------"
        )
        body = f"{full_message}{footer}"

        # Split body into 500 character segments (max GroupMe length)
        max_len = 450  # Leave some room
        segments = [body[i : i + max_len] for i in range(0, len(body), max_len)]

        # Iterate directly over the text_chunk in segments
        for text_chunk in segments:
            for bot_id in self.bot_ids:
                payload = {
                    "bot_id": bot_id,
                    "text": text_chunk,
                }
                self.webhook_client.post(payload)


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------


def dispatch(
    msg: str,
    eas_fields: Dict[str, Any],
    cfg: Settings,
    rabbitmq_publisher: Optional[RabbitMQPublisher] = None,
) -> None:
    """
    Dispatch the message to the configured destinations.

    Parameters:
    - msg (str): The message content to send.
    - eas_fields (Dict[str, Any]): A dictionary containing EAS fields to
        include in the message.
    - cfg (Settings): The runtime configuration object containing
        destination information.
    - rabbitmq_publisher (Optional[RabbitMQPublisher]): Instance for
        RabbitMQ.
    """
    processed_timestamp_utc: Optional[str] = None

    # Generic Webhooks
    if cfg.webhooks:
        # Send the raw message and full EAS fields separately
        webhook_payload = {"message_text": msg, "eas_data": eas_fields}
        for url in cfg.webhooks:
            Webhook(url).post(webhook_payload)

    # Discord
    if cfg.discord_urls and eas_fields:
        Discord(cfg.discord_urls).post(msg, eas_fields)
    elif cfg.discord_urls:  # Fallback if no `eas_fields`
        LOGGER.warning("No EAS fields, sending plain message to Discord URLs.")
        Discord(cfg.discord_urls).post(
            f"Plain message: {msg}", {"event_name": "Unknown Event"}
        )

    # GroupMe
    if cfg.groupme_bot_ids and eas_fields:
        GroupMe(cfg.groupme_bot_ids).post(msg, eas_fields)
    elif cfg.groupme_bot_ids:  # Fallback if no `eas_fields`
        LOGGER.warning("No EAS fields, sending plain message to GroupMe bot IDs.")
        GroupMe(cfg.groupme_bot_ids).post(
            f"Plain message: {msg}", {"event_name": "Unknown Event"}
        )

    # RabbitMQ
    if rabbitmq_publisher and cfg.rabbitmq_amqp_url and eas_fields:
        LOGGER.debug(
            "Publishing to RabbitMQ exchange `%s` with routing key `%s`",
            cfg.rabbitmq_exchange_name,
            cfg.rabbitmq_routing_key,
        )

        processed_timestamp_utc = datetime.now(timezone.utc).isoformat()

        rabbitmq_payload = {
            "source": "wbor-endec",
            "timestamp_processed_utc": processed_timestamp_utc,
            "message_text": msg,
            "eas_data": eas_fields,
        }
        if not rabbitmq_publisher.publish(rabbitmq_payload, cfg.rabbitmq_routing_key):
            LOGGER.error("Failed to publish message to RabbitMQ.")
    elif rabbitmq_publisher and cfg.rabbitmq_amqp_url and not eas_fields:
        LOGGER.warning("No EAS fields, publishing simplified message to RabbitMQ.")
        processed_timestamp_utc = datetime.now(timezone.utc).isoformat()
        rabbitmq_payload = {
            "source": "wbor-endec",
            "timestamp_processed_utc": processed_timestamp_utc,
            "message_text": msg,
            "eas_data": {"event_name": "Plain Text Message", "raw_header": "Not found"},
        }
        rabbitmq_publisher.publish(rabbitmq_payload, cfg.rabbitmq_routing_key)


# ---------------------------------------------------------------------------
# Serial processing loop
# ---------------------------------------------------------------------------


def process_serial(  # pylint: disable=too-many-branches, too-many-statements
    cfg: Settings, rabbitmq_publisher: Optional[RabbitMQPublisher]
) -> None:
    """
    Main event loop for processing serial input.
    This function continuously reads from the serial port and processes
    incoming News Feed messages.

    Parameters:
    - cfg (Settings): The runtime configuration object containing serial
        port information.
    - rabbitmq_publisher (Optional[RabbitMQPublisher]): Instance for
        RabbitMQ publishing.
    """

    def transform_and_send(lines: List[str]) -> None:  # pylint: disable=too-many-locals
        """
        Transform the incoming lines into a message and send it to the
        configured destinations. Uses two attempts to find a valid EAS
        header and message body with different regex patterns.

        Parameters:
        - lines (List[str]): The list of lines read from the serial
            port.
        """
        eas_fields: Dict[str, Any] = {}
        final_message_str: str = ""

        cleaned_lines = [line.strip() for line in lines if line.strip()]
        if not cleaned_lines:
            LOGGER.debug("No content in buffer to send after cleaning.")
            return

        # Attempt 1: Check for a complete header on a single line
        found_header_on_single_line = False
        single_line_header_index = -1

        for i, line in enumerate(cleaned_lines):
            if HEADER_RE.match(line):  # Strict match for the whole line
                try:
                    potential_eas_fields = parse_eas(line)
                    eas_fields = potential_eas_fields  # Parsed successfully
                    single_line_header_index = i
                    found_header_on_single_line = True
                    LOGGER.debug(
                        "Attempt 1: Found and parsed complete EAS header on line %d: %s",
                        i,
                        line,
                    )
                    break
                except ValueError:
                    # Line looked like a header but failed full validation
                    LOGGER.debug(
                        "Attempt 1: Line %s seemed like a header but failed parse_eas.",
                        line,
                    )
                    continue  # Keep checking other lines

        if found_header_on_single_line:  # pylint: disable=too-many-nested-blocks
            message_body_lines = [
                line
                for idx, line in enumerate(cleaned_lines)
                if idx != single_line_header_index
            ]
            final_message_str = " ".join(message_body_lines).strip()
        else:
            # Attempt 2: Header might be fragmented or embedded; search in concatenated content
            LOGGER.debug(
                "Attempt 1 failed. Proceeding to Attempt 2 (concatenated search for "
                "fragmented/embedded header)."
            )
            content_for_header_search = "".join(
                cleaned_lines
            )  # Join without spaces for header reconstruction

            header_match_in_joined = HEADER_SEARCH_RE.search(content_for_header_search)

            if header_match_in_joined:
                candidate_header_str = header_match_in_joined.group(0)
                try:
                    eas_fields = parse_eas(candidate_header_str)
                    LOGGER.debug(
                        "Attempt 2: Found and parsed EAS header from joined content: %s",
                        eas_fields.get("event_name", candidate_header_str),
                    )

                    # The header was found in `content_for_header_search` at
                    # `header_match_in_joined.span()`.
                    # We need to iterate through `cleaned_lines` and pick out the parts that are NOT
                    # part of the header.

                    match_span_in_concat = (
                        header_match_in_joined.span()
                    )  # (start_char_idx, end_char_idx) of header in concatenated string

                    message_fragments = []
                    current_concat_pos = 0
                    for original_line in cleaned_lines:
                        line_len = len(original_line)
                        line_concat_start = current_concat_pos
                        line_concat_end = current_concat_pos + line_len

                        # Determine parts of original_line to keep based on its position
                        # relative to the header's span in the concatenated string.

                        # Part of the line that falls BEFORE the header match segment
                        if line_concat_start < match_span_in_concat[0]:
                            # End of this pre-header segment is the earlier of line_end or
                            # header_start
                            actual_end_for_pre_segment = min(
                                line_concat_end, match_span_in_concat[0]
                            )
                            num_chars_in_pre_segment = (
                                actual_end_for_pre_segment - line_concat_start
                            )
                            if num_chars_in_pre_segment > 0:
                                message_fragments.append(
                                    original_line[:num_chars_in_pre_segment]
                                )

                        # Part of the line that falls AFTER the header match segment
                        if line_concat_end > match_span_in_concat[1]:
                            # Start of this post-header segment is the later of line_start or
                            # header_end
                            actual_start_for_post_segment = max(
                                line_concat_start, match_span_in_concat[1]
                            )
                            num_chars_in_post_segment = (
                                line_concat_end - actual_start_for_post_segment
                            )
                            if num_chars_in_post_segment > 0:
                                # Calculate slice start offset relative to the current original_line
                                slice_start_offset = (
                                    actual_start_for_post_segment - line_concat_start
                                )
                                message_fragments.append(
                                    original_line[slice_start_offset:]
                                )

                        current_concat_pos = line_concat_end

                    # Join the collected fragments with spaces.
                    # Filter out any fragments that might have become empty.
                    final_message_str = " ".join(
                        frag for frag in message_fragments if frag
                    ).strip()

                    LOGGER.debug(
                        "Attempt 2: Reconstructed message body from original lines (excluding "
                        "header): `%.200s`",
                        final_message_str,
                    )

                except ValueError:
                    LOGGER.warning(
                        "Attempt 2: String `%s` found by search did not validate as EAS header. "
                        "Treating all lines as message.",
                        candidate_header_str,
                    )
                    # Fallback: No valid header found, treat all original lines as message with
                    # spaces
                    final_message_str = " ".join(cleaned_lines).strip()
                    eas_fields = {}  # Ensure it's empty
            else:
                # No header found by either method
                LOGGER.debug(
                    "Attempt 2: No EAS header pattern found in joined content."
                )
                final_message_str = " ".join(cleaned_lines).strip()
                eas_fields = {}

        # Fallback message if parsing yielded EAS fields but no actual message body text was derived
        if not final_message_str and eas_fields:
            final_message_str = eas_fields.get("event_name", "EAS Alert (No Text Body)")
        elif not final_message_str and not eas_fields:  # No message and no header
            LOGGER.debug("No message content or EAS fields to dispatch.")
            return

        LOGGER.info(
            "Dispatching message. EAS Event: %s. Message snippet: `%s...`",
            eas_fields.get("event_name", "No EAS Header"),
            final_message_str[:100],
        )
        dispatch(final_message_str, eas_fields, cfg, rabbitmq_publisher)

    while True:  # pylint: disable=too-many-nested-blocks
        ser: Optional[Serial] = None
        try:
            LOGGER.debug("Opening serial port `%s` at 9600 baud.", cfg.port)
            ser = Serial(cfg.port, baudrate=9600, bytesize=8, stopbits=1, timeout=1)
            LOGGER.info("Serial port `%s` opened.", cfg.port)

            buffer: List[str] = []
            in_message_block = False

            while ser.is_open:
                raw_bytes = b""
                try:
                    raw_bytes = ser.readline()
                    if not raw_bytes:  # Timeout occurred, loop again
                        if (
                            in_message_block
                        ):  # If we were in a block, maybe it ended due to timeout
                            LOGGER.debug(
                                "Serial readline timed out while in message block. Processing "
                                "buffered lines."
                            )
                            if buffer:
                                transform_and_send(list(buffer))  # Send copy
                                buffer.clear()
                            in_message_block = False
                        continue

                    line = raw_bytes.decode("utf-8", errors="ignore").strip()
                    LOGGER.debug("Raw serial line: %r", line)

                    if "<ENDECSTART>" in line:
                        LOGGER.debug("Found <ENDECSTART>. Starting new message block.")
                        if (
                            buffer
                        ):  # Process any previous dangling buffer lines if a new START appears
                            LOGGER.warning(
                                "New <ENDECSTART> found with existing buffer. Processing old "
                                "buffer first."
                            )
                            transform_and_send(list(buffer))
                        buffer.clear()
                        in_message_block = True

                        # Remove the tag itself if it's the only thing on the line
                        line_content_after_start = line.split("<ENDECSTART>", 1)[
                            -1
                        ].strip()
                        if line_content_after_start:
                            buffer.append(line_content_after_start)
                        continue  # Move to next readline

                    if in_message_block:
                        if "<ENDECEND>" in line:
                            LOGGER.debug("Found <ENDECEND>. Ending message block.")
                            # Content before <ENDECEND> on the same line
                            line_content_before_end = line.split("<ENDECEND>", 1)[
                                0
                            ].strip()
                            if line_content_before_end:
                                buffer.append(line_content_before_end)

                            if buffer:
                                transform_and_send(list(buffer))
                            buffer.clear()
                            in_message_block = False
                        else:
                            if line:  # Add non-empty lines to buffer
                                buffer.append(line)
                    # else: Lines outside a block are ignored unless it's a start tag

                except SerialException as read_exc:
                    LOGGER.error(
                        "Error during serial read on `%s`: %s", cfg.port, read_exc
                    )
                    # Might indicate a disconnected device, break to outer loop to retry connection
                    break
                except UnicodeDecodeError as decode_exc:
                    LOGGER.warning(
                        "Unicode decode error for raw bytes: %r - %s",
                        raw_bytes,
                        decode_exc,
                    )
        except SerialException as conn_exc:
            LOGGER.error(
                "Failed to open or communicate with serial port `%s`: %s",
                cfg.port,
                conn_exc,
            )
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.critical(
                "Unexpected error in serial processing loop: %s", e, exc_info=True
            )
        finally:
            if ser and ser.is_open:
                ser.close()

            if (
                rabbitmq_publisher
            ):  # Check connection periodically if RabbitMQ is enabled
                try:
                    rabbitmq_publisher.ensure_connection()
                except Exception:  # pylint: disable=broad-except
                    LOGGER.exception("Periodic RabbitMQ connection check failed")

            LOGGER.info(
                "Waiting 5 seconds before retrying serial processing loop due to a failure..."
            )
            time.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:  # pylint: disable=missing-function-docstring, too-many-statements
    # Get public config
    parser = argparse.ArgumentParser(description="WBOR ENDEC Decoder & Publisher")
    parser.add_argument(
        "--config", required=True, type=Path, help="Path to public config JSON"
    )
    args = parser.parse_args()

    public_cfg: Dict[str, Any] = {}
    secrets: Dict[str, Any] = {}
    cfg: Optional[Settings] = None
    rabbitmq_publisher: Optional[RabbitMQPublisher] = None

    try:
        public_cfg = _load_json(args.config)

        # Get secrets, assuming they live at a fixed path
        secret_path_str = os.getenv("SECRETS_PATH", "/etc/wbor-endec/secrets.json")
        secret_path = Path(secret_path_str)
        if not secret_path.exists():
            LOGGER.error(
                "Secrets file not found at %s. Please create it or set SECRETS_PATH.",
                secret_path,
            )
            # No point in continuing if secrets can't be loaded for URLs etc.
            # However, if RabbitMQ URL is also in public_cfg or env vars, this logic might change.
            # For now, assuming `secrets.json` is critical.
            # Try to setup basic logging even if we exit early.
            _lazy_setup_logging(
                public_cfg.get("debug", False), public_cfg.get("logfile")
            )
            return
        secrets = _load_json(secret_path)

        cfg = Settings(public_cfg, secrets)
        _lazy_setup_logging(cfg.debug, cfg.logfile)  # Setup logging using settings

    except FileNotFoundError as e:
        # Basic logging setup if config/secrets loading fails early
        _lazy_setup_logging(public_cfg.get("debug", False), public_cfg.get("logfile"))
        LOGGER.critical("Configuration file not found: %s. Exiting.", e)
        return
    except json.JSONDecodeError as e:
        _lazy_setup_logging(public_cfg.get("debug", False), public_cfg.get("logfile"))
        LOGGER.critical("Error decoding JSON configuration: %s. Exiting.", e)
        return
    except RuntimeError as e:  # For "No destinations configured"
        _lazy_setup_logging(public_cfg.get("debug", False), public_cfg.get("logfile"))
        LOGGER.critical("Configuration error: %s. Exiting.", e)
        return
    except Exception as e:  # pylint: disable=broad-except
        _lazy_setup_logging(public_cfg.get("debug", False), public_cfg.get("logfile"))
        LOGGER.critical(
            "An unexpected error occurred during initialization: %s", e, exc_info=True
        )
        return

    LOGGER.info("wbor-endec starting on serial port `%s`", cfg.port)
    LOGGER.info(
        "Authors: Evan Vander Stoep <@evanvs>, Mason Daugherty <@mdrxy>\n"
        "Version: 4.1.1\n"
        "WBOR 91.1 FM [https://wbor.org]\n"
    )

    # Initialize RabbitMQ Publisher if configured
    if cfg.rabbitmq_amqp_url and cfg.rabbitmq_exchange_name:
        try:
            rabbitmq_publisher = RabbitMQPublisher(
                amqp_url=cfg.rabbitmq_amqp_url, exchange_name=cfg.rabbitmq_exchange_name
            )
            LOGGER.info(
                "RabbitMQ publisher initialized for exchange `%s` (routing key: `%s`).",
                cfg.rabbitmq_exchange_name,
                cfg.rabbitmq_routing_key,
            )
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.error(
                "Failed to initialize RabbitMQ publisher: `%s`. Will proceed without RabbitMQ.",
                e,
                exc_info=True,
            )
            rabbitmq_publisher = None  # Ensure it's None if init fails
    elif cfg.rabbitmq_amqp_url and not cfg.rabbitmq_exchange_name:
        LOGGER.error(
            "RabbitMQ AMQP URL provided but exchange name is missing. RabbitMQ disabled."
        )

    try:
        process_serial(cfg, rabbitmq_publisher)
    except KeyboardInterrupt:
        LOGGER.info("Keyboard interrupt received. Shutting down...")
    except (
        Exception  # pylint: disable=broad-exception-caught
    ) as e:  # Catch unexpected errors from process_serial if they escape its own try/except
        LOGGER.critical("Critical error in main processing: %s", e, exc_info=True)
    finally:
        LOGGER.info("wbor-endec shutting down...")
        if rabbitmq_publisher:
            rabbitmq_publisher.close()


if __name__ == "__main__":
    main()
