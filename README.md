# wbor-endec (FKA OpenEndec)

> [!WARNING]
> **DO NOT RELY ON THIS PROGRAM WHEN LOSS, DAMAGE, INJURY OR DEATH MAY OCCUR!**
> **ALWAYS, ALWAYS HAVE MULTIPLE METHODS TO RECEIVE EMERGENCY ALERTS.**
>
> THIS IS *NOT* AN EAS DECODER

A secure, Open Source EAS ENDEC Logger to transmit News Feed messages recieved by a Sage Digital ENDEC Hardware Unit to external destinations.

## News Feed & EAS Specs

- News Feed spec: [section 9.4 (pg. 70) of the ENDEC manual](https://www.sagealertingsystems.com/docs/digital_endec_1_0.pdf)
- EAS protocol specification: [47 CFR 11.31](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-11/subpart-B/section-11.31)

## Features

- Supports multiple concurrent transmission destinations:
  - Generic HTTP(S) webhooks
  - GroupMe bot
  - Discord embed
  - RabbitMQ
- In the future, we would be open to PRs for other destinations, such as:
  - [ ] Slack
  - [ ] Email (SMTP)
  - [ ] Twilio

## Configuration

### Public settings (`config.json`)

```json
{
  "port": "/dev/ttyUSB0",
  "logfile": "/var/log/wbor-endec/wbor-endec.log",
  "debug": false
}
```

- `port`: The device to read from. This is the `/dev/` port for your ENDEC hardware. For example, if your ENDEC is connected to `/dev/ttyUSB0`, you would use `/dev/ttyUSB0`.
- `logfile`: The (optional) path to the log file. This is where wbor-endec will write its logs if specified. Make sure the user running wbor-endec has write permissions to this file. If not specified, logs go to `stderr`/`journal`.
- `debug`: Set to `true` to enable debug level logging.

Save as `/etc/wbor-endec/config.json` (0644):

```sh
sudo mkdir -p /etc/wbor-endec
sudo cp config.json /etc/wbor-endec/config.json
sudo chmod 0644 /etc/wbor-endec/config.json
```

### Private credentials (`secrets.json`)

```json
{
  "webhooks": ["https://example.com/webhook1", "https://example.com/webhook2"],
  "discord_urls": ["https://discord.com/api/webhooks/...", "..."],
  "groupme_bot_ids": ["abcd1234", "efgh5678"],
  "rabbitmq_amqp_url": "amqp://guest:guest@localhost:5672",
  "rabbitmq_exchange_name": "wbor-endec"  // Optional, defaults to "wbor-endec"
}
```

- `webhooks`: List of webhook URLs to forward EAS messages to. These can be any HTTP(S) endpoint that accepts POST requests.
- `groupme_bot_ids`: List of GroupMe bot IDs to forward EAS messages to. These can be found in the GroupMe developer portal (public, free).
- `discord_urls`: List of Discord webhook URLs to forward EAS messages to. You can create a webhook in your Discord server channel settings.
- `rabbitmq_amqp_url`: RabbitMQ AMQP URL to forward EAS messages to. This should be in the format `amqp://username:password@hostname:port/vhost`.
- `rabbitmq_exchange_name`: The RabbitMQ exchange name to publish messages to. This is optional and defaults to `wbor-endec`.

Save in a directory managed by systemd's `LoadCredential` (e.g. `/etc/wbor-endec/`) as `secrets.json` with permissions `0600` and owner `root:root`:

```sh
sudo mkdir -p /etc/wbor-endec
sudo cp secrets.json /etc/wbor-endec/secrets.json
sudo chmod 0600 /etc/wbor-endec/secrets.json
sudo chown root:root /etc/wbor-endec/secrets.json
```

## Installation & usage

This project uses `uv` for dependency management (recommended, [installation instructions](https://docs.astral.sh/uv/getting-started/installation/)). You can also use `pip` if preferred.

### Quick Start with Makefile

For development and quick setup, use the included Makefile:

```sh
# Clone and navigate to repo
git clone https://github.com/WBOR-91-1-FM/wbor-endec && cd wbor-endec

# Install dependencies and set up development environment
make dev-setup

# Edit config.json and secrets.json with your configuration
# (config.json template will be created by dev-setup)

# Run the application
make run
```

See more on the [Development page](/development) for available Make commands and additional setup instructions.

### Manual Installation

**Example scenario:**

1. In Sage's [EndecSetD](https://www.sagealertingsystems.com/support-pc.htm), set one of the DB-9 serial COM ports on your ENDEC to output "News Feed". See [section 9.4 (pg. 70) of the ENDEC manual](https://www.sagealertingsystems.com/docs/digital_endec_1_0.pdf) for more info.
2. Use a [USB to RS-232 serial adapter](https://amzn.to/46FljxQ) to connect the News Feed COM port of your ENDEC to a computer. We're using a Raspberry Pi, though any computer that can run Python and connect to the internet should work fine.
3. Run `sudo dmesg`  (for [macOS/Linux](https://man7.org/linux/man-pages/man1/dmesg.1.html)) to identify the the `/dev/` device the adapter is plugged into. We're looking for a new USB detection for a serial device. Using the RS-232 adapter linked above, this is the log we found:

    ```sh
    usb 1-1.1: FTDI USB Serial Device converter now attached to ttyUSB0
    ```

    This is telling us that the ENDEC feed will come in at `/dev/ttyUSB0`. If you're feeling up to it, assign the device a more user-friendly name by creating a udev rule. See [this guide](https://www.rigacci.org/wiki/doku.php/doc/appunti/linux/sa/renaming_usb_devices) for more info.

4. Clone this repo and navigate to it via `git clone https://github.com/WBOR-91-1-FM/wbor-endec && cd wbor-endec`
5. Install dependencies:
   - **Using uv**: `uv sync`
6. Start monitoring the ENDEC by running the script:

    ```sh
    uv run python endec.py --config /etc/wbor-endec/config.json
    ```

    - Reads public settings from `--config` path
    - Loads secrets from `$CREDENTIALS_DIRECTORY/secrets.json` or `/etc/wbor-endec/secrets.json`
    - Validates port and URLs, then begins reading `<ENDECSTART>…<ENDECEND>` payloads and forwards messages

### Running as a Systemd Service

```sh
sudo nano /etc/systemd/system/wbor-endec.service
```

Reference the template at `wbor-endec.service`.

You will need to change:

- `{DEVICE}` = the `/dev/` device you found in step 3, e.g. `/dev/ttyUSB0`. IMPORTANT NOTE: Systemd derives the device unit name from the path by converting slashes to dashes, e.g. `/dev/ttyENDEC` → `dev-ttyENDEC.device`. So, if your device is `/dev/ttyUSB0`, the unit name will be `dev-ttyUSB0.device`. You can check this by running `systemctl list-units --type=device` and looking for your device.
- `Environment=SECRETS_PATH={PATH_TO_SECRETS_JSON}` = path to the `secrets.json` file you created in the previous step (`/etc/wbor-endec/secrets.json`). This is where the script will look for the credentials.
- Under `ExecStart`
  - `{PYTHON3_EXEC_PATH}` = directory for your Python executable, e.g. `/home/username/wbor-endec/.venv/bin/python`
  - `{SCRIPT_PATH}` = path to `endec.py`, e.g. `/home/username/wbor-endec/endec.py`
  - `{PATH_TO_CONFIG_JSON}` = the path to the `config.json` file you created in the previous step (`/etc/wbor-endec/config.json`)
- `WorkingDirectory` = path to the `wbor-endec` repo folder you cloned, e.g. `/home/username/wbor-endec`
- `User` = username for the user running wbor-endec (e.g. `pi` for Raspberry Pi)

After saving, run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable wbor-endec.service
sudo systemctl start wbor-endec.service
sudo systemctl status wbor-endec.service
```

Look for `active (running)` and the `wbor-endec.log` file to confirm it is up and running.

After updating (pulling from this repo), be sure to run:

```sh
sudo systemctl daemon-reload
sudo systemctl restart wbor-endec.service
```

## Troubleshooting & Logs

If the service fails to start or continuously restarts, view live output with:

```sh
sudo journalctl -u wbor-endec.service -f
```

To inspect the last 100 entries:

```sh
sudo journalctl -u wbor-endec.service -n 100
```

## TODO

- [ ] Add support for setting different timezones (for users in different timezones)

Maintained by WBOR 91.1 FM and [Mason Daugherty](https://github.com/mdrxy), originally inspired by [Evan Vander Stoep](https://github.com/evanvs).
