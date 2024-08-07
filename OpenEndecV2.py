import os, json, sys, argparse, requests, serial, time, logging
from serial import Serial
from serial.serialutil import SerialException

logging.basicConfig(
    filename="openendec.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

content = ""

parser = argparse.ArgumentParser()
parser.add_argument(
    "-c",
    "--com",
    dest="port",
    default="/dev/ttyUSB0",
    help="Select the port the device is on. (e.g. /dev/ttyUSB0)",
    required=True,
)
parser.add_argument(
    "-w",
    "--webhook",
    dest="webhook",
    nargs="+",
    default="",
    help="Webhook(s) to send to.",
    required=True,
)
args = parser.parse_args()
port = args.port
webhooks = args.webhook


def main():
    global content
    payload = {"content": content}
    logging.info("Payload: %s", payload)
    logging.info("Sending to Webhooks: %s", webhooks)

    header_data = {"content-type": "application/json"}
    for webhook in webhooks:
        try:
            response = requests.post(webhook, json.dumps(payload), headers=header_data)
            response.raise_for_status()
            logging.info("Successfully posted to %s", webhook)
        except requests.exceptions.RequestException as e:
            logging.error("Failed to post to %s: %s", webhook, e)
    content = ""


def newsFeed():
    serialText = ""
    dataList = []
    global content
    activeAlert = False

    while True:
        try:
            ser = serial.Serial(port=port, baudrate=9600, bytesize=8, stopbits=1)
            logging.info("Connected to serial port %s", port)
            if ser.isOpen():
                while True:
                    serialText = ser.readline().decode("utf-8")
                    if "<ENDECSTART>" in serialText:
                        activeAlert = True
                    elif "<ENDECEND>" in serialText:
                        content = "".join(dataList)
                        dataList = []
                        activeAlert = False
                        main()
                    else:
                        if activeAlert:
                            dataList.append(serialText)
        except SerialException as e:
            logging.error("Serial exception: %s", e)
        except Exception as e:
            logging.error("Unexpected error: %s", e)
        finally:
            if ser.isOpen():
                ser.close()
                logging.info("Closed serial port %s", port)
            logging.info("Reconnecting to serial port...")
            # Sleep or wait before reconnecting
            time.sleep(5)


if __name__ == "__main__":
    logging.info(
        "OpenENDEC V2\nOriginally Written By: Evan Vander Stoep [https://github.com/EvanVS]\nModified by: Mason Daugherty [https://github.com/mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\nLogger Started!"
    )
    newsFeed()
