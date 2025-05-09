"""
Decode NewsFeed EAS messages from a Sage Digital ENDEC and forward them
to a webhook URL or a GroupMe group.

Authors:
    - Evan Vander Stoep <@evanvs>
    - Mason Daugherty <@mdrxy>

Version: 2.1.1
Last Modified: 2025-03-23

Changelog:
    - 1.0.0 (????): Initial release <@evanvs>
    - 2.0.0 (2021-02-22): Second release <@evanvs>
    - 2.1.0 (2024-08-08): Refactored for better readability and added
        support for GroupMe <@mdrxy>
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

message_content = ""
eas = ""

parser = argparse.ArgumentParser()
parser.add_argument(
    "-c",
    "--com",
    dest="port",
    default="/dev/ttyUSB0",
    help="Select the port the device is on. Default is /dev/ttyUSB0",
)
parser.add_argument(
    "-w", "--webhook", dest="webhookUrls", nargs="+", help="Webhook URL(s) to send to."
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
        "Trim the EAS message from the body before sending, destroying it."
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
        "Trim the human readable text from the message before sending. destroying it."
        'ONLY the EAS message will be sent (as "message").'
    ),
)

args = parser.parse_args()
requiredArgs = {"webhook": "webhookUrls", "groupme": "groupmeBotId"}

if not any(getattr(args, arg) for arg in requiredArgs.values()):
    ARG_LIST = ", ".join([f"--{arg}" for arg in requiredArgs.keys()])
    parser.error(
        f"At least one of the following arguments must be provided: {ARG_LIST}"
    )

if args.debug:
    logging.basicConfig(level=logging.DEBUG)


def parse_eas(eas_str):
    """
    Parse the EAS string to extract the event and location.
    # ex: ZCZC-ORG-EEE-PSSCCC+TTTT-JJJHHMM-LLLLLLLL-
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


class Webhook:
    """
    Generic class for sending messages to a webhook URL.
    """

    def __init__(self, url=None, eas=None):
        self.headers = {"Content-Type": "application/json"}
        self.url = url
        self.eas = eas

    def post(self, message_content):
        """
        Generic POST request to a webhook URL.

        Parameters:
        - message_content (str): The message to send to the webhook.
        """
        self.payload = {"message": message_content}

        if self.eas:
            self.payload["eas"] = self.eas

        logging.info(
            "Making POST to %s with payload: %s", self.url, json.dumps(self.payload)
        )
        response = requests.post(
            self.url, headers=self.headers, json=json.dumps(self.payload), timeout=10
        )
        logging.info("Response from %s: %s", self.url, response.text)


class GroupMe(Webhook):
    """
    Operations for sending messages to a GroupMe group via a Bot.
    """

    def post(self, message_content):
        """
        Post message to a GroupMe group via a Bot.
        """

        self.url = "https://api.groupme.com/v3/bots/post"

        footer = (
            "\n\nThis message was sent using OpenENDEC V2.1"
            "[github/WBOR-91-1-FM/wbor-endec]\n----------"
        )
        body = f"{message_content}{footer}"

        # Split body into 500 character segments (max length for GroupMe messages)
        segments = [body[i : i + 500] for i in range(0, len(body), 500)]

        for segment in segments:
            # Forward to all bots specified
            for bot_id in args.groupmeBotId:
                # Schema: https://dev.groupme.com/docs/v3#bots_post
                self.payload = {"bot_id": bot_id, "text": segment}

                logging.debug("Making POST to GroupMe with payload: %s", self.payload)
                logging.info("Making POST to GroupMe")
                response = requests.post(
                    self.url, headers=self.headers, json=self.payload, timeout=10
                )
                if response.text:
                    logging.error("GroupMe's response: %s", response.text)
                else:
                    logging.info("GroupMe POST successful")


def post():
    """
    Send News Feed object message payload to specified webhooks.

    Raises:
    - requests.exceptions.RequestException: If the request to a webhook
        fails.
    """
    global message_content
    global eas

    # Post to each webhook URL provided
    if args.webhookUrls:
        for url in args.webhookUrls:
            Webhook(url, eas).post(message_content)

    # Post to GroupMe if bot ID is provided
    if args.groupmeBotId:
        GroupMe().post(message_content)

    message_content = ""
    eas = ""


def newsfeed():
    """
    Continuously decodes News Feed objects from the provided serial
    port.

    Raises:
    - serial.SerialException: If the serial connection fails.
    """
    serial_text = ""
    data_list = []
    global message_content
    global eas
    active_alert = False
    i = 0

    while True:
        try:
            ser = Serial(args.port, baudrate=9600, bytesize=8, stopbits=1)
            logging.info("Connected to serial port %s", args.port)
            if ser.isOpen():
                while True:
                    serial_text = ser.readline().decode("utf-8").strip()
                    if "<ENDECSTART>" in serial_text:
                        active_alert = True
                    elif "<ENDECEND>" in serial_text:
                        if args.trim:
                            data_list.pop()

                        if args.fork:
                            eas = data_list.pop()

                        if args.quiet:
                            data_list = [data_list[-1]]

                        message_content = " ".join(data_list)
                        data_list = []
                        active_alert = False
                        i = 0
                        post()
                    else:
                        if active_alert:
                            data_list.append(serial_text)
                            logging.debug("Line #%d: %s", i, serial_text)
                            i += 1
        except (SerialException, requests.exceptions.RequestException) as e:
            logging.error("Handled error: %s", e)
        finally:
            if ser.isOpen():
                ser.close()
                logging.info("Closed serial port %s", args.port)
            logging.info("Reconnecting to serial port...")
            time.sleep(5)  # Wait before trying to reconnect


if __name__ == "__main__":
    logging.info(
        "OpenENDEC V2.1\n"
        "Originally Written By: Evan Vander Stoep [https://github.com/EvanVS]\n"
        "Modified by: Mason Daugherty [@mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\n"
        "Logger Started!\nLogs will be stored at %s",
        LOGFILE,
    )
    newsfeed()
