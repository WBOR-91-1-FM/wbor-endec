# OpenEndec V2.1

> [!WARNING]
> **DO NOT RELY ON THIS PROGRAM WHEN LOSS, DAMAGE, INJURY OR DEATH MAY OCCUR!**
> **ALWAYS, ALWAYS HAVE MULTIPLE METHODS TO RECEIVE EMERGENCY ALERTS.**
>
> THIS IS *NOT* AN EAS DECODER

An Open Source EAS ENDEC Logger to transmit News Feed messages recieved by a Sage Digital ENDEC Hardware Unit to a webhook/external destination.

News Feed spec: [section 9.4 (pg. 70) of the ENDEC manual](https://www.sagealertingsystems.com/docs/digital_endec_1_0.pdf)

EAS protocol specification: [47 CFR 11.31](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-11/subpart-B/section-11.31)

## Features

- Supports multiple concurrent transmission destinations
- Argument validation

**Destinations:**

- Generic webhook
- GroupMe
- [ ] Discord

In the future, we would be open to PRs for other destinations, such as:

- [ ] Slack
- [ ] Email (SMTP)
- [ ] Twilio

## Options

`--com / -c`: The device to read from. This is the `/dev/` port for your ENDEC hardware. For example, if your ENDEC is connected to `/dev/ttyUSB0`, you would use `-c /dev/ttyUSB0`.

`--trim / -t`: Trim the EAS message from the body before sending, which is always the final line. EAS messages follow the [format](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-11/subpart-B/section-11.31): `ZCZC-ORG-EEE-PSSCCC + TTTT-JJJHHMM-LLLLLLLL-`

`--quiet / -q`: Trim the human readable message body, leaving ONLY the EAS message.

### Destinations

`--webhook / -w {URL_1, URL_2, ...}`: Webhook URL(s) to forward EAS messages to.

`--groupme / -g {BOT_ID_1, BOT_ID_2, ...}`: GroupMe bot(s) to forward EAS messages to.

## Installation

**Example scenario:**

1. In Sage's [EndecSetD](https://www.sagealertingsystems.com/support-pc.htm), set one of the DB-9 serial COM ports on your ENDEC to output "News Feed". See [section 9.4 (pg. 70) of the ENDEC manual](https://www.sagealertingsystems.com/docs/digital_endec_1_0.pdf) for more info.
2. Use a [USB to RS-232 serial adapter](https://amzn.to/46FljxQ) to connect the News Feed COM port of your ENDEC to a computer. We're using a Raspberry Pi, though any computer that can run Python and connect to the internet should work fine.
3. Run `sudo dmesg`  (for [macOS/Linux](https://man7.org/linux/man-pages/man1/dmesg.1.html)) to identify the the `/dev/` device the adapter is plugged into. We're looking for a new USB detection for a serial device. Using the RS-232 adapter linked above, this is the log we found:

    ```sh
    usb 1-1.1: FTDI USB Serial Device converter now attached to ttyUSB0
    ```

    This is telling us that the ENDEC feed will come in at `/dev/ttyUSB0`. If you're feeling up to it, assign the device a more user-friendly name by creating a udev rule. See [this guide](https://www.rigacci.org/wiki/doku.php/doc/appunti/linux/sa/renaming_usb_devices) for more info.

4. Clone this repo and navigate to it via `git clone https://github.com/WBOR-91-1-FM/wbor-endec && cd wbor-endec`
5. Install necessary dependencies via `pip install -r requirements.txt`, or, better yet, activate a virtual environment for this repo *and then* install the dependencies.
6. Start monitoring the ENDEC by running the script:

    ```sh
    python3 OpenEndecV2.py -c {YOUR-DEVICE} {OPTIONS}
    ```

    {YOUR DEVICE} is the `/dev/` port we found in step 3. So, in this example, we use `/dev/ttyUSB0`.

    [{OPTIONS}](#options) specifies the [destinations](#destinations) OpenEndec should forward EAS messages to.

### Running as a System Service

```sh
sudo nano /etc/systemd/system/wbor-endec.service
```

Reference the template at `wbor-endec.service`.

You will need to change:

- `{DEVICE}` = the `/dev/` device you found in step 3, e.g. `/dev/ttyUSB0`. IMPORTANT NOTE: Systemd derives the device unit name from the path by converting slashes to dashes, e.g. /dev/ttyENDEC → dev-ttyENDEC.device. So, if your device is `/dev/ttyUSB0`, the unit name will be `dev-ttyUSB0.device`. You can check this by running `systemctl list-units --type=device` and looking for your device.
- Under `ExecStart`
  - `{PYTHON_EXEC}` = directory for your Python executable, e.g. `/usr/bin/python3` or, if using a virtual environment, `/home/username/wbor-endec/venv/bin/python` (in most cases, you'll need to make one and then install the dependencies with `pip install -r requirements.txt`)
  - `{SCRIPT_PATH}` = path to `OpenEndecV2.py`, e.g. `/home/username/wbor-endec/OpenEndecV2.py`
  - `{OPTIONS}` = options for the scripts with respective arguments, e.g. `--groupme {BOT_ID}`
- `WorkingDirectory` = path to the `wbor-endec` repo folder you cloned, e.g. `/home/username/wbor-endec`
- `User` = username for the user running OpenEndec (e.g. `pi` for Raspberry Pi)

**Device Unit Naming:**

After saving, run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable wbor-endec.service
sudo systemctl start wbor-endec.service
sudo systemctl status wbor-endec.service
```

Look for `active (running)` and the `openendec.log` file to confirm it is up and running.

After updating (pulling from this repo), be sure to run:

```sh
sudo systemctl daemon-reload
sudo systemctl restart wbor-endec.service
```
