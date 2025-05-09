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
import time

import requests
from serial import Serial
from serial.serialutil import SerialException

LOGFILE = "openendec.log"

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s : %(message)s",
)

parser = argparse.ArgumentParser()
parser.add_argument(
    "-c",
    "--com",
    dest="port",
    default="/dev/ttyUSB0",
    help="Select the port the device is on. Default is /dev/ttyUSB0",
)
parser.add_argument(
    "-w",
    "--webhook",
    dest="webhookUrls",
    nargs="+",
    help="Webhook URL(s) to send to.",
)
parser.add_argument(
    "-g",
    "--groupme",
    dest="groupmeBotId",
    nargs="+",
    help="Send ENDEC messages to a GroupMe Group. Pass in the bot ID to use.",
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
requiredArgs = {"webhook": "webhookUrls", "groupme": "groupmeBotId"}

if not any(getattr(args, arg) for arg in requiredArgs.values()):
    ARG_LIST = ", ".join(f"--{opt}" for opt in requiredArgs)
    parser.error(
        f"At least one of the following arguments must be provided: {ARG_LIST}"
    )

if args.debug:
    logging.basicConfig(level=logging.DEBUG)


def parse_eas(eas_str):
    """
    Parse the EAS string to extract the event and location.
    ex: ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-
    """
    parts = eas_str.split("-")
    event = parts[2]  # EEE
    location = parts[3]  # PSSCCC
    return event, location


EAS_EVENT_NAMES = {
    "RWT": "Required Weekly Test",
    "RMT": "Required Monthly Test",
    "EAN": "Emergency Action Notification",
    # Add more as needed
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
        logging.info("Response from `%s`: %s", self.url, response.text)
        return response


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
            "[github/WBOR-91-1-FM/wbor-endec]\n----------"
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
                    logging.error("GroupMe's response: %s", response.text)
                else:
                    logging.info("GroupMe POST successful")

        return responses


def post_message(
    message_content: str,
    eas: str,
    webhook_urls: list = None,
    groupme_bot_ids: list = None,
) -> None:
    """
    Send News Feed object message payload to specified webhooks.

    Parameters:
    - message_content (str): The message content to send.
    - eas (str): The EAS message if available.
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
            Webhook(url=url, eas=eas).post(message_content)

    # Post to GroupMe if bot IDs are provided
    if groupme_bot_ids:
        GroupMe(bot_ids=groupme_bot_ids).post(message_content)


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
        eas = ""
        if process_args.trim and lines:
            lines.pop()
        if process_args.fork and lines:
            eas = lines.pop()
        if process_args.quiet and lines:
            lines = [lines[-1]]
        message = " ".join(lines)
        post_message(
            message_content=message,
            eas=eas,
            webhook_urls=process_args.webhookUrls,
            groupme_bot_ids=process_args.groupmeBotId,
        )

    while True:
        ser = None
        try:
            ser = Serial(process_args.port, baudrate=9600, bytesize=8, stopbits=1)
            logging.debug("Connected to serial port %s", process_args.port)

            while ser.isOpen():
                # Wait for start marker
                raw = ser.readline()
                line = raw.decode("utf-8", errors="ignore")
                if "<ENDECSTART>" not in line:
                    continue

                # Collect lines until reaching the end marker
                buffer = []
                for raw2 in iter(ser.readline, b""):
                    chunk = raw2.decode("utf-8", errors="ignore")
                    if "<ENDECEND>" in chunk:
                        break
                    buffer.append(chunk.strip())

                # Process the collected lines
                if buffer:
                    transform_and_post(buffer)

        except (SerialException, requests.exceptions.RequestException) as exc:
            logging.error("Handled error: %s", exc)
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
