# OpenEndec V2.1

An Open Source EAS ENDEC Logger, tuned for college radio stations such as [WBOR 91.1 FM](https://wbor.org).

This repo contains scripts to log alerts recieved by the Sage Digital ENDEC Hardware Unit to a webhook/external destination.

[Info about the EAS protocol specification](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-11/subpart-B/section-11.31)

## Options

### Generic Webhook

```sh
-w {URL_1, URL_2, ...}
```

Webhook URL(s) to forward EAS messages to.

### GroupMe

```sh
-g {BOT_ID_1, BOT_ID_2, ...}
```

GroupMe bot(s) to forward EAS messages to.

## Installation

1. In Sage's [EndecSetD](https://www.sagealertingsystems.com/support-pc.htm), set one of the DB-9 serial COM ports on your ENDEC to output "News Feed". See [section 9.4 (pg. 70) of the ENDEC manual](https://www.sagealertingsystems.com/docs/digital_endec_1_0.pdf) for more info.
2. Use a [USB to RS-232 serial adapter](https://amzn.to/46FljxQ) to connect the News Feed COM port of your ENDEC to a computer. We're using a [Raspberry Pi Zero 2 W](https://amzn.to/3WEdPX7) (with a [Micro USB to female USB-A adapter](https://amzn.to/3WzT27f)), though any computer that can run Python and connect to the internet should work fine.
3. Run `sudo dmesg`  (for [macOS/Linux](https://man7.org/linux/man-pages/man1/dmesg.1.html)) to identify the the `/dev/` device the adapter is plugged into. We're looking for a new USB detection for a serial device. Using the RS-232 adapter linked above, this is the log we found:

    ```sh
    usb 1-1.1: FTDI USB Serial Device converter now attached to ttyUSB0
    ```

    This is telling us that the ENDEC feed will come in at `/dev/ttyUSB0`.

4. Clone this repo and navigate to it via `git clone https://github.com/WBOR-91-1-FM/wbor-endec && cd wbor-endec`
5. Install necessary dependencies via `pip3 install -r requirements.txt`, or, better yet, activate a virtual environment for this repo and then install the dependencies.
6. Start monitoring the ENDEC by running the script:

    ```sh
    python3 OpenEndecV2.py -c {YOUR-DEVICE} {OPTIONS}
    ```

    {YOUR DEVICE} is the `/dev/` port we found in step 3. So, in this example, we use `/dev/ttyUSB0`.

    {OPTIONS} specifies the destinations OpenEndec should forward EAS messages to.

## To-do

* Argument validation
