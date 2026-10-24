# simpleaprs

APRS for Linux without the config files. Built-in 1200 baud sound-card modem, offline vector maps from a
Project N.O.M.A.D. server. Runs as a Qt desktop app, or as a terminal server with a web page (for a Pi on a radio).

    uv venv && uv pip install -e '.[all,dev]'
    uv run simpleaprs        # the desktop app; first run opens the setup dialog
    uv run pytest

The install has two optional parts: `[gui]` (PySide6, the desktop app) and `[server]` (sounddevice, the terminal server).

- **Radio**: sound card in/out (Qt Multimedia, no PortAudio). PTT is VOX only; "VOX lead-in"
  sets how long the preamble runs before data so VOX is open in time.
- **Maps**: N.O.M.A.D. server URL in Settings (default http://nomad.local:8080). The app proxies
  its MapLibre style and PMTiles locally, since N.O.M.A.D. sends no CORS headers. Settings > "Cache map tiles" keeps what
  it fetches in `~/.cache/simpleaprs/tiles` (size-limited, least recently used goes first), so tiles load fast and the
  map still draws when the server is unreachable. A changed map file on the server drops its cached tiles.
- Forces the X11 (xcb) Qt platform: WebGL hangs in QtWebEngine under native Wayland here.
- **Sharing**: Settings > "Share this radio on the network" serves what the radio hears over HTTP (default port 8073).
  Another simpleaprs set to "Connect to another simpleaprs" (server address + token) follows it: same map, stations,
  packets and messages, no radio needed. The token is required to watch unless you untick it, and always required to
  transmit; clients can only ask for a beacon or a message, never send raw frames. Traffic is plain HTTP, so keep it
  on your LAN or a VPN (e.g. Tailscale). Remembered stations for a followed server are kept separately from local ones.

- **Settings** are in tabs: Station, Radio, Alerts, Map, Network, iGate, Digipeater.
- **iGate** (desktop app, with a local radio): Settings > iGate gates what the radio hears to an APRS-IS server (default
  `rotate.aprs2.net:14580`). You need the APRS-IS passcode for your callsign (the Calculate button works it out). Packets that
  came from the internet, are third-party, or carry `NOGATE`/`RFONLY` are never gated, and each packet goes out once. Your own
  position is sent to the server every 30 minutes. Optionally it also puts internet *messages* on the air, but only for
  stations this radio heard directly in the last 30 minutes, at most 10 a minute (off by default; it makes your radio transmit).
- **Digipeater** (off by default): repeats packets that ask for it. Fill-in mode answers `WIDE1-1` and your own callsign;
  wide-area mode also answers `WIDE2-1`/`WIDE2-2`. Each hop used is replaced by your callsign, the same packet is never
  repeated twice within 30 seconds, and the transmission waits a random fraction of a second so neighbouring digipeaters
  don't key up together. Only worth running where your radio reaches ground other digipeaters don't.
  Both are for the desktop app; the terminal server doesn't run them yet.

## Terminal / Pi server

`--headless` (or `--server`) runs with no window, no Qt and no display: it listens to the radio and serves a **web page**
you open from any browser on the network (phone, laptop). Built for a Pi attached to a radio.

    sudo apt install libportaudio2          # the sound library (Debian / Raspberry Pi OS)
    pipx install '/path/to/simpleaprs[server]'   # or: pip install '.[server]'  (PySide6 is not needed)
    simpleaprs --headless                   # first run asks a few questions and saves them, then serves
    simpleaprs --headless --setup           # go through the questions again (current values are the defaults)
    simpleaprs --list-devices               # audio device names, as the options below expect them

(`python -m simpleaprs --headless` works too.) It prints the web address, e.g. `http://raspberrypi.local:8073/`, and logs
packets heard and sent (`-q` hides packets). Ctrl+C stops it. Settings live in the same `~/.config/simpleaprs/config.json`
as the desktop app, but audio device names differ between the two (PortAudio vs Qt), so run `--setup` on the Pi.

The page has the same map as the desktop app plus the station list, messages and raw packets, a beacon button and a message
box. Clicking a station on the map opens its popup, and that has a **Message** button that opens the message box addressed
to it. Below the message box is a bulletin box: pick BLN0-BLN9 and send text to everyone (a bulletin is a message to
`BLN#` with no message number, so nobody acks it; sending the same number again replaces the old one). The desktop app has
the same popup button and bulletin box. Bulletins heard on the air (to `BLN#`, with or without a group name) are listed in the Messages tab as
`BLN1 from CALL: text`, once per sender and text since senders repeat them, with no ack and no alert sound. The "Show" menu
at the top of the tab narrows the list to direct messages or bulletins (remembered between visits). The desktop app can also follow it (Settings > Connect to another simpleaprs, address only, no token).

**There is no login.** Anyone who can reach the port can watch and transmit through your radio, so keep it on your LAN
or a VPN such as Tailscale, never on the internet. As a guard against *other web pages* using your browser to reach it,
the server only answers requests addressed to an IP address, `localhost`, `*.local`, or a one-word host name, and refuses
cross-site and non-JSON requests; if you reach it by another name, add `--allow-host that.name`. `--no-tx` makes it
receive-only.

Everything can also be given as options, which apply to that run only unless `--save` is added. With `--callsign` there
are no questions, so it works from a script or a service:

    simpleaprs --headless --callsign N0CALL-7 --lat 39.50 --lon -98.35 \
        --input-device "USB Audio" --output-device "USB Audio" --nomad-url http://nomad.local:8080 --save

Other options: `--symbol --comment --path --lead-ms --beacon-minutes --port --no-tx --allow-host` (see `--help`). A device
name is matched exactly or as part of the full name, and one that isn't found is an error rather than a silent fall back
to the default. Map tiles are cached like in the desktop app (`~/.cache/simpleaprs/tiles`).

To start it at boot, run it as a user service once it has been set up from a terminal:

    # ~/.config/systemd/user/simpleaprs.service
    [Service]
    ExecStart=/path/to/simpleaprs --headless
    Restart=on-failure
    [Install]
    WantedBy=default.target

    systemctl --user enable --now simpleaprs && loginctl enable-linger $USER
