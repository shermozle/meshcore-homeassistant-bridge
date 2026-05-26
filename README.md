# meshcore-homeassistant-bridge

A **Meshcore Companion bot** that bridges direct messages on your LoRa mesh
network to **Home Assistant** commands.  Connects to your
[pyMC_Repeater](https://github.com/pyMC-dev/pyMC_Repeater) instance over TCP,
authenticates senders by public key, and exposes a keyword-based command interface
for controlling lights, switches, climate, and querying entity states.

## Architecture

```
Your phone (on mesh)
    │
    ▼  LoRa — DM: "light kitchen on"
┌──────────────────────────┐
│  pyMC_Repeater (Unraid)  │  ← has the LoRa radio (SPI SX1262)
│  CompanionFrameServer    │  ← exposes companion protocol on TCP:5000
└──────────┬───────────────┘
           │
           ▼  TCP:5000 (Meshcore companion binary protocol)
┌──────────────────────────┐
│  meshcore-ha-bridge      │  ← this container
│  (companion client)      │
└──────────┬───────────────┘
           │
           ▼  HTTPS REST API (long-lived token)
┌──────────────────────────┐
│  Home Assistant :8123    │
└──────────────────────────┘
```

pyMC_Repeater's `CompanionFrameServer` speaks the standard Meshcore companion
binary protocol — the bridge connects as a regular companion client using the
[`meshcore`](https://pypi.org/project/meshcore/) Python library, the same way
`meshcore-cli` or a phone app would.

## Quick Start

### 1. Get a Home Assistant long-lived token

In Home Assistant, go to **Settings → Your Profile → Security → Long-Lived
Access Tokens** and create a token.  Copy it — you'll only see it once.

### 2. Find your Meshcore public key

You need the 64-character hex public key of any node you want to authorise
(your phone, your partner's phone, etc.).

**From your mesh client (phone):** look in the node/device info for the
public key — often displayed as a 64-char hex string.

**From pyMC's web dashboard:** open `http://<repeater-ip>:8000`, go to the
Companions tab, and find the public key of the companion your phone is
connected through.  You can also use `pymc-cli` (installed with pyMC_Repeater).

### 3. Create `config.yaml`

Copy `config.yaml.example` to `config.yaml` and fill in:

```yaml
meshcore:
  host: "192.168.1.100"   # IP of your pyMC_Repeater container/host
  port: 5000              # CompanionFrameServer TCP port

home_assistant:
  url: "http://192.168.1.50:8123"
  token: "eyJhbGciOi...your-token"

allowlist:
  - pubkey: "a1b2c3d4...64 hex chars...a1b2"
    name: "your-phone"
```

### 4. Run with Docker

```bash
docker compose up -d
```

Or directly:

```bash
docker build -t meshcore-ha-bridge .
docker run -d --name meshcore-ha-bridge \
  --restart unless-stopped \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  meshcore-ha-bridge
```

### 5. On Unraid

- Place the project directory somewhere on your Unraid shares
  (e.g. `/mnt/user/appdata/meshcore-ha-bridge/`).
- In the Docker tab, use **Add Container**, point it at the directory, or use
  the **Docker Compose** plugin.
- Mount `config.yaml` as a read-only volume.
- If pyMC_Repeater is running in another Docker container, put both containers
  on the same Docker network so they can reach each other by container name or
  internal IP.
- Make sure pyMC_Repeater's `CompanionFrameServer` is enabled (it listens on
  TCP port 5000 by default — check your pyMC config).

## Commands

Send any of these as a DM to the bridge's node on the mesh:

| Command | Effect |
|---------|--------|
| `help` | Returns this command reference |
| `light <name> on` | Turn a light on (fuzzy-matched by name) |
| `light <name> off` | Turn a light off |
| `light <name> toggle` | Toggle a light |
| `switch <name> on/off/toggle` | Control a switch |
| `climate set <temp>` | Set thermostat temperature (first climate entity) |
| `status <entity_id or name>` | Get current state of an entity |
| `lights` | List all lights and their states |
| `switches` | List all switches and their states |
| `all` | Show entity counts by domain |

Entity names are **fuzzy-matched** — `light kitchen` matches `light.kitchen_ceiling`
if "kitchen" appears in its friendly name.

## Adding More Users

When an unauthorised node sends a DM, the bridge replies with their
public key prefix.  Copy that prefix, look up the full 64-char key
(e.g. by having the user send it to you), and add it to `config.yaml`:

```yaml
allowlist:
  - pubkey: "f6e5d4c3...64 hex chars...f6e5"
    name: "partner-phone"
```

Then restart the container (`docker compose restart`).

## Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `meshcore.transport` | `tcp` | `tcp` or `serial` |
| `meshcore.host` | `127.0.0.1` | pyMC / companion radio IP (TCP mode) |
| `meshcore.port` | `5000` | TCP port |
| `meshcore.serial_port` | `/dev/ttyUSB0` | Serial device (serial mode) |
| `meshcore.baud_rate` | `115200` | Serial baud rate |
| `meshcore.debug` | `false` | Enable meshcore protocol debug logging |
| `home_assistant.url` | *(required)* | HA instance URL (no trailing slash) |
| `home_assistant.token` | *(required)* | Long-lived access token |
| `home_assistant.request_timeout` | `10.0` | API call timeout in seconds |
| `allowlist` | *(required)* | List of `{pubkey, name}` entries |
| `dm_reply_max_chars` | `200` | Max reply length (Meshcore limit) |

## Logs

```bash
docker logs -f meshcore-ha-bridge
```

Logs show every DM received (with sender name, hop count, and command text),
auth rejections, and HA API errors.

## Development

```bash
# Install locally
pip install -e .

# Run directly (needs config.yaml in working directory)
python -m meshcore_ha_bridge
```
