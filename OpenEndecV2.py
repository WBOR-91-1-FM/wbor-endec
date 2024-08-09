import os, json, sys, argparse, requests, serial, time, logging
from serial import Serial
from serial.serialutil import SerialException

LOGFILE = "openendec.log"

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s : %(message)s",
)

messageContent = ""

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

group = parser.add_mutually_exclusive_group()
group.add_argument(
    "-t",
    "--trim",
    dest="trim",
    action="store_true",
    default=False,
    help="Trim the EAS message from the body before sending.",
)
group.add_argument(
    "-q",
    "--quiet",
    dest="quiet",
    action="store_true",
    default=False,
    help="Trim the human readable text from the message before sending.",
)

args = parser.parse_args()
requiredArgs = {"webhook": "webhookUrls", "groupme": "groupmeBotId"}

if not any(getattr(args, arg) for arg in requiredArgs.values()):
    argList = ", ".join([f"--{arg}" for arg in requiredArgs.keys()])
    parser.error(f"At least one of the following arguments must be provided: {argList}")


class Webhook:
    def __init__(self, url=None):
        self.headers = {"Content-Type": "application/json"}
        self.url = url

    def post(self, messageContent):
        self.payload = {"message": messageContent}

        logging.info(
            "Making POST to %s with payload: %s", self.url, json.dumps(self.payload)
        )
        response = requests.post(
            self.url, headers=self.headers, json=json.dumps(self.payload)
        )
        logging.info("Response from %s: %s", self.url, response.text)


class GroupMe(Webhook):
    def post(self, messageContent):
        self.url = "https://api.groupme.com/v3/bots/post"

        footer = "\n\nThis message was sent using OpenENDEC V2.1 [github/WBOR-91-1-FM/wbor-endec]\n----------"
        body = f"{messageContent}{footer}"

        # Split body into 500 character segments (max length for GroupMe messages)
        segments = [body[i : i + 500] for i in range(0, len(body), 500)]

        for segment in segments:
            # Forward to all bots specified
            for bot_id in args.groupmeBotId:
                # Schema: https://dev.groupme.com/docs/v3#bots_post
                self.payload = {"bot_id": bot_id, "text": segment}

                logging.info("Making POST to GroupMe with payload: %s", self.payload)
                response = requests.post(
                    self.url, headers=self.headers, json=self.payload
                )
                logging.info("GroupMe's response: %s", response.text)


def post():
    """
    Send News Feed object message payload to specified webhooks.

    Raises:
        requests.exceptions.RequestException: If the request to a webhook fails.
    """
    global messageContent

    # Post to each webhook URL provided
    if args.webhookUrls:
        for url in args.webhookUrls:
            Webhook(url).post(messageContent)

    # Post to GroupMe if bot ID is provided
    if args.groupmeBotId:
        GroupMe().post(messageContent)

    messageContent = ""


def newsFeed():
    """
    Continuously decodes News Feed objects from the provided serial port.

    Raises:
        serial.SerialException: If the serial connection fails.
    """
    serialText = ""
    dataList = []
    global messageContent
    activeAlert = False
    i = 0

    while True:
        try:
            ser = serial.Serial(args.port, baudrate=9600, bytesize=8, stopbits=1)
            logging.info("Connected to serial port %s", args.port)
            if ser.isOpen():
                while True:
                    serialText = ser.readline().decode("utf-8").strip()
                    if "<ENDECSTART>" in serialText:
                        activeAlert = True
                    elif "<ENDECEND>" in serialText:
                        if args.trim:
                            dataList[:-1]

                        if args.quiet:
                            dataList = [dataList[-1]]

                        messageContent = "".join(dataList)
                        dataList = []
                        activeAlert = False
                        i = 0
                        post()
                    else:
                        if activeAlert:
                            dataList.append(serialText)
                            logging.info("Line #%d: %s", i, serialText)
                            i += 1
        except SerialException as e:
            logging.error("Serial exception: %s", e)
        except Exception as e:
            logging.error("Unexpected error: %s", e)
        finally:
            if ser.isOpen():
                ser.close()
                logging.info("Closed serial port %s", args.port)
            logging.info("Reconnecting to serial port...")
            time.sleep(5)  # Wait before trying to reconnect


if __name__ == "__main__":
    logging.info(
        f"OpenENDEC V2\nOriginally Written By: Evan Vander Stoep [https://github.com/EvanVS]\nModified by: Mason Daugherty [https://github.com/mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\nLogger Started!\nLogs will be stored at {LOGFILE}"
    )
    newsFeed()
