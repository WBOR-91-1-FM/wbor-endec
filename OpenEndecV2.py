import os, json, sys, argparse, requests, serial
from serial import Serial
from serial.serialutil import SerialException

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
    "--webhooks",
    dest="webhook",
    nargs="+",
    default="",
    help="Webhook(s) to send to.",
    required=True,
)
args = parser.parse_args()
port = args.port
webhooks = args.webhooks


def main():
    global content
    payload = {"content": content}
    header_data = {"content-type": "application/json"}
    for webhook in webhooks:
        requests.post(webhook, json.dumps(payload), headers=header_data)
    content = ""


def newsFeed():
    serialText = ""
    dataList = []
    global content
    ser = serial.Serial(port=port, baudrate=9600, bytesize=8, stopbits=1)
    if ser.isOpen():
        while True:
            serialText = str(ser.readline())
            if "<ENDECSTART>" in serialText:
                activeAlert = True
            elif "<ENDECEND>" in str(serialText):
                content = "".join(dataList)
                del dataList[:]
                activeAlert = False
                main()
                break
            else:
                if activeAlert == True:
                    dataList.append(serialText)
                else:
                    pass
    else:
        print("Serial Port in use or non-existent.")

print(
    "OpenENDEC V2\nOriginally Written By: Evan Vander Stoep [https://github.com/EvanVS]\nModified by: Mason Daugherty [https://github.com/mdrxy] for WBOR 91.1 FM [https://wbor.org]\n\nLogger Started!"
)
newsFeed()
