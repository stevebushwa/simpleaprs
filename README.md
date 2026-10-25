# simpleaprs

APRS for Linux without the config files. Built-in 1200 baud sound-card modem, offline vector maps from a
Project N.O.M.A.D. server. Runs as a Qt desktop app, or as a terminal server with a web page (for a Pi on a radio).

    uv venv && uv pip install -e '.[all,dev]'
    uv run simpleaprs        # the desktop app; first run opens the setup dialog
    uv run pytest

## Platforms

| | Desktop app | Terminal server (`--headless`) |
|---|---|---|
| Linux | AppImage, or `pip install` | yes: Raspberry Pi and other single-board computers |
| Windows | zip: unpack and run `simpleaprs.exe` | no |
| macOS (Apple silicon and Intel) | zip: unpack and open `simpleaprs.app` | no |

The server is Linux only on purpose: it is meant to sit on a single-board computer next to a radio. The Windows and macOS
apps are for the other end, a computer you follow that server from (Settings > Network > Connect to another simpleaprs).
They can also drive a radio plugged into that computer's own sound card, but that hasn't been tried on those systems yet.
Builds come from the `build` workflow: every push builds and tests them, and pushing a tag like `v0.2.0` attaches them to a
GitHub release. They are not code-signed, so the first launch needs one extra step: on Windows, SmartScreen's "More info"
then "Run anyway"; on macOS, right-click the app and choose Open (or `xattr -dr com.apple.quarantine simpleaprs.app`).

The install has two optional parts: `[gui]` (PySide6, the desktop app) and `[server]` (sounddevice, the terminal server).

- **Radio**: sound card in/out (Qt Multimedia, no PortAudio). PTT is VOX only; "VOX lead-in"
  sets how long the preamble runs before data so VOX is open in time.
- **Maps**: N.O.M.A.D. server URL in Settings (blank until you set it, and then there is no map, only stations). The app proxies
  its MapLibre style and PMTiles locally, since N.O.M.A.D. sends no CORS headers. Settings > "Cache map tiles" keeps what
  it fetches in `~/.cache/simpleaprs/tiles` (size-limited, least recently used goes first), so tiles load fast and the
  map still draws when the server is unreachable. A changed map file on the server drops its cached tiles.
- **Legend and tracks**: the map's top-left legend says what the colours mean (collapse it with its title). A station that
  moves leaves a green track behind it (a new point once it has moved about 15 m, the last 300 kept, saved with the
  remembered stations). Stations shown grey were not heard since the program started; **Clear inactive (N)** in the legend
  forgets them (after asking) from the map, the list and the saved file. A followed server still remembers them and sends
  them again the next time you connect.
- Forces the X11 (xcb) Qt platform: WebGL hangs in QtWebEngine under native Wayland here.
- **Sharing**: Settings > "Share this radio on the network" serves what the radio hears over HTTP (default port 8073).
  The RX level meter follows too: a client shows the audio level of the radio it is watching.
  Another simpleaprs set to "Connect to another simpleaprs" (server address + token) follows it: same map, stations,
  packets and messages, no radio needed. The token is required to watch unless you untick it, and always required to
  transmit; clients can only ask for a beacon or a message, never send raw frames. Traffic is plain HTTP, so keep it
  on your LAN or a VPN (e.g. Tailscale). Remembered stations for a followed server are kept separately from local ones.

- **Settings** are in tabs: Station, Radio, Alerts, Map, Network, iGate, Digipeater.
- **iGate** (desktop app with a local radio, or the terminal server): Settings > iGate gates what the radio hears to an APRS-IS server (default
  `rotate.aprs2.net:14580`). You need the APRS-IS passcode for your callsign (the Calculate button works it out). Packets that
  came from the internet, are third-party, or carry `NOGATE`/`RFONLY` are never gated, and each packet goes out once. Your own
  position is sent to the server every 30 minutes. Optionally it also puts internet *messages* on the air, but only for
  stations this radio heard directly in the last 30 minutes, at most 10 a minute (off by default; it makes your radio transmit).
- **Digipeater** (off by default): repeats packets that ask for it. Fill-in mode answers `WIDE1-1` and your own callsign;
  wide-area mode also answers `WIDE2-1`/`WIDE2-2`. Each hop used is replaced by your callsign, the same packet is never
  repeated twice within 30 seconds, and the transmission waits a random fraction of a second so neighbouring digipeaters
  don't key up together. Only worth running where your radio reaches ground other digipeaters don't.
  Both also run in the terminal server (below), where they are the natural fit for a Pi that stays on.

## Terminal / Pi server

`--headless` (or `--server`) runs with no window, no Qt and no display: it listens to the radio and serves a **web page**
you open from any browser on the network (phone, laptop). Built for a Pi attached to a radio, and set up entirely from
that page.

    sudo apt install libportaudio2          # the sound library (Debian / Raspberry Pi OS)
    pipx install '/path/to/simpleaprs[server]'   # or: pip install '.[server]'  (PySide6 is not needed)
    simpleaprs --headless                   # starts straight away, even with nothing set up yet
    simpleaprs --list-devices               # audio device names, as the options below expect them

(`python -m simpleaprs --headless` works too.) It prints the web address, e.g. `http://raspberrypi.local:8073/`, and logs
packets heard and sent (`-q` hides packets). Ctrl+C stops it.

**First visit.** The page asks you to create a login (a username and a password of 8 or more characters). Whoever opens
it first makes the account, so do that before sharing the address. After that it asks for the login. **Remember me on this
device** keeps you logged in for 30 days (each visit renews it); without it you are logged out when the browser closes, or
after 12 hours. Five wrong passwords lock that address out for 30 seconds. Passwords are stored hashed (scrypt), sessions
as hashes, in `~/.config/simpleaprs/auth.json`, readable only by you. Forgot it? `simpleaprs --headless --reset-login`
forgets the account so the next visitor creates a new one.

**Settings** is the page's last tab, and holds what the desktop app's Settings does: Station (callsign, position, symbol,
comment, path, auto beacon), Radio (sound cards, VOX lead-in, transmit level), Map, iGate, Digipeater and Access. Changes
take effect when you press Save: the sound card is reopened, the gateways restarted, and so on, with no restart of the
server. If the sound card can't be opened the server keeps running and the page says why, so you can pick another. Settings
live in `~/.config/simpleaprs/config.json`, shared with the desktop app, but audio device names differ between the two
(PortAudio vs Qt), so choose them on the page rather than copying them over.

**Desktop apps** don't log in. They follow the server with its **access token**, shown (and replaceable) under Settings >
Access: in the desktop app, Settings > Network > Connect to another simpleaprs, with the server's address and that token.
Changing the token cuts off apps using the old one.

The page has the same map as the desktop app plus the station list, messages and raw packets, a beacon button and a message
box. Clicking a station on the map opens its popup, and that has a **Message** button that opens the message box addressed
to it. Below the message box is a bulletin box: pick BLN0-BLN9 (or BLNX for urgent) and send text to everyone (a bulletin is
a message to `BLN#` with no message number, so nobody acks it; sending the same number again replaces the old one). The
desktop app has the same popup button and bulletin box. Bulletins heard on the air (to `BLN#`, with or without a group
name) are listed in the Messages tab as `BLN1 from CALL: text`, once per sender and text since senders repeat them, with no
ack and no alert sound. The "Show" menu at the top of the tab narrows the list to direct messages or bulletins (remembered
between visits).

**It is plain HTTP.** The login keeps other people on your network, and other web pages, out; it doesn't hide anything from
someone who can watch the network, so keep the server on your LAN or a VPN such as Tailscale, never on the internet. As a
guard against *other web pages* using your logged-in browser to reach it, the server only answers requests addressed to an
IP address, `localhost`, `*.local`, or a one-word host name, and refuses cross-site and non-JSON requests; if you reach it by
another name, add `--allow-host that.name`.

Everything on the Settings page can also be given as options, which apply to that run only unless `--save` is added, or
asked in the terminal with `--setup`. With `--callsign` there are no questions, so it works from a script or a service:

    simpleaprs --headless --callsign N0CALL-7 --lat 39.50 --lon -98.35 \
        --input-device "USB Audio" --output-device "USB Audio" --nomad-url http://nomad.local:8080 --save

**iGate and digipeater** on the server: turn them on in Settings, or use `--igate --igate-passcode CODE` (plus
`--igate-server HOST:PORT`, `--igate-to-rf`, `--igate-radius KM`) and `--digipeater fill|wide|off`; `--no-igate` etc. turn them
off again for a run. The startup banner shows what is on, and a line is logged whenever the iGate connects or drops (the page
shows it too). These transmit (digipeating, and internet messages to RF) on their own account: `--no-tx` (or "Let the web
page and desktop apps transmit" in Access) only stops the web page and clients from transmitting, it does not turn them off.
Packets the server repeats or gates to RF are marked "relayed" in the feed, so a desktop app following the server doesn't
list them as your own messages.

Other options: `--symbol --comment --path --lead-ms --beacon-minutes --port --no-tx --allow-host` (see `--help`). A device
name is matched exactly or as part of the full name. Map tiles are cached like in the desktop app (`~/.cache/simpleaprs/tiles`).

To start it at boot, run it as a user service (it needs no setup first, and the page does the rest):

    # ~/.config/systemd/user/simpleaprs.service
    [Service]
    ExecStart=/path/to/simpleaprs --headless
    Restart=on-failure
    [Install]
    WantedBy=default.target

    systemctl --user enable --now simpleaprs && loginctl enable-linger $USER
