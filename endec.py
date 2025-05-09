"""
Decode NewsFeed EAS messages from a Sage Digital ENDEC and forward them
to a webhook URL or a GroupMe group.

Authors:
    - Evan Vander Stoep <@evanvs>
    - Mason Daugherty <@mdrxy>

Version: 2.1.2
Last Modified: 2025-05-08

Changelog:
    - 1.0.0 (????): Initial release <@evanvs>
    - 2.0.0 (2021-02-22): Second release <@evanvs>
    - 2.1.0 (2024-08-08): Refactored for better readability and added
        support for GroupMe <@mdrxy>
    - 2.1.2 (2025-05-08): Refactor
"""

import argparse
import json
import logging
import os
import stat
import time
from urllib.parse import urlparse

import requests
from serial import Serial
from serial.serialutil import SerialException

LOGFILE = "openendec.log"

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s : %(message)s",
)


def valid_port(path: str) -> str:
    """
    Validate the provided serial port path.

    Parameters:
    - path (str): The path to the serial port.

    Returns:
    - str: The validated serial port path.

    Raises:
    - argparse.ArgumentTypeError: If the path does not exist or is not a
        character device.
    """
    if not os.path.exists(path):
        raise argparse.ArgumentTypeError(f"Serial port '{path}' not found")
    if not stat.S_ISCHR(os.stat(path).st_mode):
        raise argparse.ArgumentTypeError(f"{path} exists but isn't a character device")
    return path


def valid_url(u: str) -> str:
    """
    Validate the provided URL.

    Parameters:
    - u (str): The URL to validate.

    Returns:
    - str: The validated URL.

    Raises:
    - argparse.ArgumentTypeError: If the URL is invalid.
    """
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise argparse.ArgumentTypeError(f"Invalid webhook URL: {u!r}")
    return u


parser = argparse.ArgumentParser()
parser.add_argument(
    "-c",
    "--com",
    dest="port",
    default="/dev/ttyUSB0",
    type=valid_port,
    help="Select the port the device is on. Default is /dev/ttyUSB0",
)
parser.add_argument(
    "-w",
    "--webhook",
    dest="webhookUrls",
    nargs="+",
    type=valid_url,
    help="Webhook URL(s) to send to.",
)

parser.add_argument(
    "-g",
    "--groupme",
    dest="groupmeBotId",
    nargs="+",
    help="Send ENDEC messages to a GroupMe Group(s). Pass in the bot ID(s) to use.",
)
parser.add_argument(
    "-D",
    "--discord",
    dest="discordUrls",
    nargs="+",
    type=valid_url,
    help="Discord webhook URL(s). Will post an embed with EAS fields.",
)
parser.add_argument(
    "-d",
    "--debug",
    dest="debug",
    action="store_true",
    default=False,
    help="Enable debug logging.",
)

group = parser.add_mutually_exclusive_group()
group.add_argument(
    "-t",
    "--trim",
    dest="trim",
    action="store_true",
    default=False,
    help=(
        "Trim the EAS message from the body before sending, destroying it. "
        '"message" will contain the human readable text ONLY.'
    ),
)
group.add_argument(
    "-f",
    "--fork",
    dest="fork",
    action="store_true",
    default=False,
    help=(
        'Trim the EAS message from the body and send it as "eas" in the webhook '
        'payload. "message" will contain the human readable text.'
    ),
)
group.add_argument(
    "-q",
    "--quiet",
    dest="quiet",
    action="store_true",
    default=False,
    help=(
        "Trim the human readable text from the message before sending. destroying it. "
        'ONLY the EAS message will be sent (as "message").'
    ),
)

args = parser.parse_args()
if not (args.webhookUrls or args.groupmeBotId or args.discordUrls):
    parser.error("You must provide at least one of: --webhook, --groupme, or --discord")

if args.debug:
    logging.basicConfig(level=logging.DEBUG)


def parse_eas(header: str) -> dict:
    """
    Parse the EAS header into its components.

    Ex: ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-
    Returns a dict with:
    - orig:   Originator code (ORG)
    - event:  Event code (EEE)
    - event_name: Human name, if known
    - location:  PSSCCC
    - duration:  TTTT (minutes)
    - start:     JJJHHMM (Julian day + HHMM)
    - id:        LLLLLLLL (unique identifier)
    """
    # Strip start/stop markers and trailing dash
    parts = header.strip("-").split("-")
    orig, event, location_and_rest = parts[1], parts[2], parts[3]

    # Location and time are joined by '+'
    location, rest = location_and_rest.split("+", 1)
    duration, timestamp, unique_id = rest.split("-", 2)

    # Map event code to human name
    name = EAS_EVENT_NAMES.get(event, "Unknown Event")
    return {
        "orig": orig,
        "event": event,
        "event_name": name,
        "location": location,
        "duration": duration,
        "start": timestamp,
        "id": unique_id,
    }


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


class Webhook:  # pylint: disable=too-few-public-methods
    """
    Generic class for sending messages to a webhook URL.
    """

    def __init__(self, url: str = None, eas: str = None, headers: dict = None) -> None:
        """
        Initialize Webhook instance.

        Parameters:
        - url (str, optional): Webhook URL. Defaults to None.
        - eas (str, optional): EAS message. Defaults to None.
        - headers (dict, optional): Custom headers. Defaults to json.
        """
        self.headers = headers or {"Content-Type": "application/json"}
        self.url = url
        self.eas = eas

    def post(self, message_content: str) -> requests.Response:
        """
        Generic POST request to a webhook URL.

        Parameters:
        - message_content (str): The message to send to the webhook.

        Returns:
        - requests.Response: Response from the webhook.

        Raises:
        - requests.exceptions.RequestException: If the request fails.
        """
        payload = {"message": message_content}

        if self.eas:
            payload["eas"] = self.eas

        logging.info(
            "Making POST to `%s` with payload: %s", self.url, json.dumps(payload)
        )
        response = requests.post(
            self.url, headers=self.headers, json=payload, timeout=10
        )
        logging.debug("Response from `%s`: %s", self.url, response.text)
        return response


class Discord:  # pylint: disable=too-few-public-methods
    """
    Send a Discord embed to one or more webhook URLs.
    """

    def __init__(self, urls: list):
        self.urls = urls

    def post(self, content: str, eas_fields: dict) -> None:
        """
        Send an embed with a description and fields:
        - Event name & code
        - Location
        - Duration
        - Start timestamp
        - Original ID
        """
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
                    "value": eas_fields.get("location", "n/a"),
                    "inline": True,
                },
                {
                    "name": "Duration",
                    "value": f"{eas_fields.get('duration')} min",
                    "inline": True,
                },
                {
                    "name": "Start",
                    "value": eas_fields.get("start", "n/a"),
                    "inline": True,
                },
                {"name": "ID", "value": eas_fields.get("id", "n/a"), "inline": False},
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        payload = {"embeds": [embed]}
        for url in self.urls:
            logging.info("Posting EAS to Discord webhook %s", url)
            response = requests.post(url, json=payload, timeout=10)
            logging.debug("Response from `%s`: %s", url, response.text)


class GroupMe:  # pylint: disable=too-few-public-methods
    """
    Operations for sending messages to a GroupMe group via a Bot.
    """

    def __init__(self, bot_ids: list, headers: dict = None) -> None:
        """
        Initialize GroupMe instance.

        Parameters:
        - bot_ids (list): List of GroupMe bot IDs.
        - headers (dict, optional): Custom headers. Defaults to None.
        """
        self.url = "https://api.groupme.com/v3/bots/post"
        self.bot_ids = bot_ids
        self.headers = headers or {"Content-Type": "application/json"}

    def post(self, message_content: str) -> list:
        """
        Post message to a GroupMe group via a Bot ID.

        Parameters:
        - message_content (str): The message to send to GroupMe.

        Returns:
        - list: List of responses from GroupMe API for each bot.

        Raises:
        - requests.exceptions.RequestException: If any request fails.
        """
        footer = (
            "\n\nThis message was sent using OpenENDEC V2\n"
            "[WBOR-91-1-FM/wbor-endec]\n----------"
        )
        body = f"{message_content}{footer}"

        # Split body into 500 character segments (max length for GroupMe messages)
        segments = [body[i : i + 500] for i in range(0, len(body), 500)]
        responses = []

        for segment in segments:
            # Forward to all bots specified
            for bot_id in self.bot_ids:
                # Schema: https://dev.groupme.com/docs/v3#bots_post
                payload = {"bot_id": bot_id, "text": segment}
                logging.debug("Making POST to GroupMe with payload: %s", payload)
                logging.info("Making POST to GroupMe")
                response = requests.post(
                    self.url, headers=self.headers, json=payload, timeout=10
                )
                responses.append(response)
                if response.text:
                    logging.debug("GroupMe's response: %s", response.text)
                else:
                    logging.info("GroupMe POST successful")

        return responses


def post_message(
    message_content: str,
    eas_fields: dict,
    webhook_urls: list = None,
    groupme_bot_ids: list = None,
    discord_urls: list = None,
) -> None:
    """
    Send News Feed object message payload to specified destinations.

    Parameters:
    - message_content (str): The message content to send.
    - eas (str): The EAS message, if available.
    - webhook_urls (list, optional): List of webhook URLs. Defaults to
        None.
    - groupme_bot_ids (list, optional): List of GroupMe bot IDs.
        Defaults to None.

    Raises:
    - requests.exceptions.RequestException: If any request to a webhook f
        ails.
    """
    # Post to each webhook URL provided
    if webhook_urls:
        for url in webhook_urls:
            Webhook(url=url, eas=eas_fields).post(message_content)

    # Post to GroupMe if bot IDs are provided
    if groupme_bot_ids:
        GroupMe(bot_ids=groupme_bot_ids).post(message_content)

    if discord_urls:
        Discord(discord_urls).post(message_content, eas_fields)


def process_newsfeed(process_args: argparse.Namespace) -> None:
    """
    Continuously decodes News Feed objects from the provided serial
    port.

    Parameters:
    - process_args: Namespace with attributes port, trim, fork, quiet,
        webhookUrls, groupmeBotId.

    Raises:
    - serial.SerialException: If the serial connection fails.
    - requests.exceptions.RequestException: If any webhook request
        fails.
    """

    def transform_and_post(lines: list) -> None:
        """
        Transform the lines of the EAS message according to the
        specified arguments and post to webhooks.
        """
        eas_fields = {}
        if process_args.trim and lines:
            # Remove the final line (EAS message), don't save it
            lines.pop()
        if process_args.fork and lines:
            # Remove the final line (EAS message) and pass in to post
            eas_header = lines.pop()
            logging.debug("About to parse EAS header: %r", eas_header)
            eas_fields = parse_eas(eas_header)
            logging.debug("Parsed EAS fields: %s", eas_fields)
        if process_args.quiet and lines:
            # Remove human readable text, only keep EAS message
            lines = [lines[-1]]
        message = " ".join(lines)
        post_message(
            message_content=message,
            eas_fields=eas_fields,
            webhook_urls=process_args.webhookUrls,
            groupme_bot_ids=process_args.groupmeBotId,
            discord_urls=process_args.discordUrls,
        )

    while True:
        ser = None
        try:
            ser = Serial(process_args.port, baudrate=9600, bytesize=8, stopbits=1)
            logging.debug("Serial port opened on `%s`", process_args.port)

            while ser.isOpen():
                logging.debug("Entering read loop on %s", process_args.port)

                raw = ser.readline()
                logging.debug("Raw input: %r", raw)

                line = raw.decode("utf-8", errors="ignore")
                if "<ENDECSTART>" not in line:
                    continue

                if "<ENDECSTART>" in line:
                    logging.debug("Found <ENDECSTART> in line: %r", line)

                # Collect lines until reaching the end marker
                buffer = []
                for raw2 in iter(ser.readline, b""):
                    chunk = raw2.decode("utf-8", errors="ignore")
                    if "<ENDECEND>" in chunk:
                        break
                    buffer.append(chunk.strip())

                # Process the collected lines
                if buffer:
                    logging.debug(
                        "Collected %d lines for one EAS payload: %s",
                        len(buffer),
                        buffer,
                    )
                    transform_and_post(buffer)

        except (SerialException, requests.exceptions.RequestException) as exc:
            logging.debug("Exception caught, will retry: %s", exc, exc_info=True)
        finally:
            if ser and ser.isOpen():
                ser.close()
                logging.info("Closed serial port %s", process_args.port)
            logging.info("Reconnecting to serial port...")
            time.sleep(5)


if __name__ == "__main__":
    logging.info(
        "OpenENDEC V2\n"
        "Originally Written By: Evan Vander Stoep [https://github.com/EvanVS]\n"
        "Modified by: Mason Daugherty [@mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\n"
        "Logger Started!\nLogs will be stored at %s",
        LOGFILE,
    )
    process_newsfeed(args)
