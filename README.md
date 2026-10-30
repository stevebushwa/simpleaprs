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
  transmit; clients can only ask for a beacon or a message, never send raw frames. Traffic is https with a certificate the
  server makes for itself (plain http with `--http`), so keep it on your LAN or a VPN (e.g. Tailscale). Remembered stations for a followed server are kept separately from local ones.

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

If `direwolf` is installed (`sudo apt install direwolf`) the server also runs it as a second decoder, receive only, on the same
audio: it recovers more packets from weak signals, and a packet either finds is shown once (`SIMPLEAPRS_DIREWOLF=0` turns it
off). A packet that arrives but fails its checksum, with a readable header, is not thrown away: the Raw packets tab shows it in
grey italics ending `<-- DAMAGED`, with whatever text came through (the end is the part most likely to be wrong; a damaged packet that was used for something says so: `DAMAGED, heard` or `DAMAGED, on map`). It is never passed
to the iGate or digipeater, never answers a message, and never moves a station that has been decoded cleanly.

What a damaged packet may do is set by three independent switches under **Settings > Receive**; with all three off they are ignored.

| Switch | What it lets a damaged packet do |
|---|---|
| Show packets that arrive damaged in the Raw packets tab (on by default) | appear there, ending `<-- DAMAGED` |
| Count stations we know as heard | refresh the last-heard time of a station decoded cleanly before, if the packet's callsigns are ones decoded cleanly and its position agrees with the known one (`DAMAGED, heard`) |
| Put stations on the map, dimmed, when enough copies agree | put a station nobody has decoded cleanly on the map, marked unverified, once **Copies that must agree** damaged packets from it put it in the same spot (**Same spot means within** metres). Not for moving (Mic-E) stations. A clean packet from it replaces the guess (`DAMAGED, on map`) |

Each switch also needs the text to be well-formed APRS up to the last few bytes, where the damage usually is. On two recordings
of a weak digipeater (two hours) this put the one station that was never decoded cleanly on the map after three copies and counted
20 damaged packets as heard; raising the copies or lowering the distance makes it stricter.

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
between visits). The Raw packets tab keeps a line for every packet as it comes in, newest at the bottom, and its own "Show"
menu picks how far back the list looks: the last hour, 6 or 12 hours, a day (the default), a week, or all of it. Every line carries
its date and time. A page is sent the last day of packets when it connects; when the menu reaches further back than that, it
asks the server for the rest as needed, so a long history doesn't slow the page down. The server keeps a month of packets (one
drops out once it is a month old) and messages for good, however many packets come after them. Both are saved in
`~/.config/simpleaprs` (`web-events.jsonl` and `frames.jsonl`; a packet takes about 150 to 250 bytes, so a busy month can run to
tens of MB) so a restart doesn't empty the page. They are written ten events at a time (a message at once; everything when the
server stops, or every 30 seconds) and rewritten smaller when a file passes 2 MB, which also drops what has aged out.

**Frequencies.** The **Frequencies** button next to the title drops down a quick-reference card: APRS (144.390 and the HF
frequency), the ISS (voice repeater, APRS, crew contacts), FM simplex calling frequencies, weak-signal calling frequencies,
HF digital (FT8, JS8Call, WSPR, PSK31, RTTY, SSTV), the Hurricane Watch Net, NOAA Weather Radio and the LoRa mesh channels. Type in its
search box (`iss`, `2 m`, `ft8`, `146.52`) to narrow it. It lists US (IARU Region 2) frequencies and says when they were checked; the
list is plain data in `simpleaprs/web/frequencies.js`.

**Searching and the map.** The Raw packets tab has a search box that narrows the lines to those containing what you type (the
callsign, a word, a time); it looks within the span the Show menu picks, so choose a longer one to search further back. The box
at the top of the page finds a station by callsign, or a word from its comment, and shows it on the map with its route. Click
any station on the map, or its row in the list, and its popup and route are drawn; click your own icon and lines are drawn to
every station you hear directly, with no digipeater between (judged by each station's last packet), and listed in your popup.
A click anywhere else on the map clears whatever is drawn. In the Stations list, click Station, Heard or Distance to sort by
callsign, when it was last heard or how far it is from you (miles), and click again to turn the order round.

**What a station says about itself.** Click a station on the map or in the list and its popup shows what its last position
packet says beyond where it is: transmit power, antenna height, gain and direction (from `PHG`), range (`RNG`), signal strength
(`DFS`), speed and course (also from Mic-E), altitude (`/A=`), a frequency with its tone and offset, and, written out in the
comment, power ("25W"), gain ("6dBi"), height ("40ft AGL", "antenna 12m"), battery voltage ("13.8V"), an email address or a web
address. A code on the packet wins over the same thing written in words, and a number that is only likely to be one of these
("2m", "5V" in a callsign) is left alone.

**Sending a text message.** Messages tab: pick Message, give "To callsign" and the text (up to 67 characters), Send. The button
stays grey until there is something to send, and it says which kind it is: switching the first menu to Bulletin changes the
form (a coloured top edge, a bulletin number instead of a callsign, "Send bulletin") and asks you to confirm before it goes to
everyone, then returns to Message. A message goes out through the radio with a number, once (nothing resends it), and a station
that gets it answers with an ack, which you'll see as a packet in the Raw packets tab. Anyone in radio range, or reached by a
digipeater on your path, can receive it; past that it needs an iGate that hears you to pass it on to the internet, and the
person at the other end to be on the internet or in range of an iGate of their own. Transmitting needs your amateur license.

To send an SMS to a phone, address the message to `SMS` and start the text with `@` and the 10-digit number (no leading 1):
`@9876543210 No cell coverage. Get me on the radio!` (the 67 characters cover the number too). The service is run by volunteers
(see aprs.wiki), covers the US, Canada, Australia and the UK, and asks the person who will receive texts to opt in on its site
first. It reaches the phone through an iGate that hears you. A reply comes back to your callsign, and only reaches your radio
if an iGate has heard you recently; the person replying should start with `@` and your callsign, since the service forgets who
you texted when it restarts.

**When a message arrives.** A card appears at the top middle of the map for each message or bulletin that comes in (with Reply for a
message), and stays until you dismiss it; one that arrived while the page was shut or the phone was asleep gets its card when the
page reconnects. The Alerts button at the top turns on a chime, a vibration, and a notification through the browser when the
page isn't the tab in front (remembered per browser). Settings > Notifications picks, for this browser, which of those happen,
whether messages and bulletins both count, and has a button to send a test. Browsers only allow notifications, and the service worker they need on a
phone, on https pages (or localhost), so over plain `http://` on the LAN you get the chime and vibration while the page is
open, and the status line says why notifications are off. A page that the phone has put to sleep can't hear the server: waking
it brings back the cards for what it missed. Alerts while the page is closed would need real push notifications, which this
doesn't do.

**Conversations.** Direct messages are kept in a folder for whoever they are with, newest conversation last, closed until you
open it (a bold name means something new arrived while it was closed). Opening one addresses the reply form to that station.
Bulletins stay in the list between the folders.

**It is https with a certificate it made for itself.** The first time, the browser warns that it can't vouch for the certificate:
continue past the warning (the page is then a "secure context", which browsers require before they allow notifications). The
certificate is made once with `openssl` and kept in `~/.config/simpleaprs/tls`; delete the folder to make a new one, or run
with `--http` for plain http. Typing `http://` still works: it is sent on to `https://`. The encryption hides the traffic from
someone listening in, but without a certificate anyone trusts it doesn't prove who you are talking to, and the login only
keeps other people on your network, and other web pages, out; keep the server on your LAN or a VPN such as Tailscale, never
on the internet. If a phone's browser still refuses notifications because of the certificate, install it on the phone
as a trusted certificate: Settings > Access > Certificate downloads it, with the steps for Android, iPhone and computers. As a
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

## simplemesh: a web page for a MeshCore node

`simplemesh` is the same idea for [MeshCore](https://meshcore.co.uk): a Pi with a **companion node plugged into its USB
serial port** serves a web page you open from any browser on the network. It is its own program: it shares no code or
files with simpleaprs. What the two web pages have in common (the login, the web server and event stream, the
N.O.M.A.D. map proxy and its tile cache) lives in a third package, `piwebkit`, that both use. The three sit in one repo
for now, with one rule that `tests/test_independence.py` enforces: apps import `piwebkit`, `piwebkit` imports no app, and
the two apps never import each other, so either can be split out or run alone.

    pipx install '/path/to/simpleaprs[mesh]'     # or: pip install '.[mesh]'
    simplemesh --list-ports                      # the serial devices that could be the node
    simplemesh                                   # https://raspberrypi.local:8074/ (--http for plain http)
    simplemesh --demo                            # no radio: a made-up mesh, to look at the page

The node must run the **companion (USB serial)** firmware. The page has: a map of every node that has shared a position
(companions, repeaters, room servers and sensors each with their own badge; click one for details and the route its
messages take, resolved from the repeaters' hashes), **Channels** and **Direct** chat (with unread counts and delivery
status: direct messages are retried and show "delivered" once acknowledged), a **Nodes** list, a live **Packets** log of
everything the radio hears (adverts, channel traffic, hops, SNR/RSSI), and **Settings**. Nodes the radio heard but your
node hasn't saved are shown dashed with an **Add** button; the node's contact list and channels stay on the node.

A node in companion serial mode can't also talk to the phone app, so the page does what the app does:

- **Settings > Node**: name and position, advertising, who may ask for your telemetry and position, ask-before-adding,
  contact links (export yours, import a friend's), statistics, restart, and back up / restore the node's key.
- **Settings > Radio**: frequency, bandwidth, spreading factor, coding rate and power, with regional presets. It warns
  when a node in North America is set to a European frequency.
- **Settings > Channels**: add (public `#name` or private with a secret), edit, share and remove channels.
- **Settings > Firmware**: update an **ESP32** node (Heltec, LilyGO, XIAO ESP32...) to the newest MeshCore companion USB
  release from GitHub, or from a `-merged.bin` file you pick when the machine has no internet. The page lets go of the
  serial port, runs esptool, and picks the node up again; it works even when the node's firmware isn't answering. nRF52
  boards (RAK4631, T114...) use a different bootloader and aren't handled.
- **Keeping contacts and routes**: this page remembers every node it hears (key, position, last heard, the last route the
  node had to it), so nothing is lost when the node forgets a contact or restarts. Nodes show grey on the map and in the list
  until they have been heard since the node connected (and again after a day of quiet). **Settings > Node > Space on the
  node** shows how full the node's contact list is and takes contacts it hasn't heard from in N days off the node while keeping
  them here (**automatic**, on by default: when the node is 90% full it takes off the contacts heard from least recently, down to
  75%, leaving favourites, people you have messaged and nodes you added by hand this week; the header shows how full it
  is, red from 80%); a node's popup has **Remove from node** (kept here, grey, can be added back on its old route), **Forget**
  (drop it here too) and **Use last route** (give the node back a route it lost). **Find route** asks a node for the path
  to it and back; repeaters, room servers and sensors tend to answer only someone logged in to them. A node's signal shown
  in its popup is the last hop's unless it was heard direct.
- **Settings > Alerts**: a card on the map, a chime, a vibration and a notification through the system for each message
  that arrives (the Alerts button at the top turns them on; the choices are per browser). On a phone, install the page as
  an app (Android: browser menu > Install app; iPhone: Share > Add to Home Screen) and the notifications come through the
  phone's own. Nothing arrives while the page or app is closed: that would take push notifications from a server on the
  internet.
- **Repeaters, room servers and sensors** (click one on the map, then Manage): log in, status, neighbours, telemetry,
  access list, a command line, and room chat. Everything goes over the radio, so it takes seconds and fails out of range.
  Passwords are passed through, never stored.

It has its own login (`~/.config/simplemesh/auth.json`; `--reset-login` forgets it), its own session cookie, and the same
rules as the APRS page: https with a certificate the server makes for itself (`~/.config/simplemesh/tls`; Settings >
Connection downloads it to install on a device, which stops the browser's warning and allows notifications and installing
the page as an app), for a LAN or VPN, and requests must be addressed to an IP, `localhost`, `*.local` or a
one-word host name (`--allow-host` adds others). On a phone the panel under the map has a button in its tab bar that
minimizes it to the tabs or opens it again; on a computer its edge is dragged to any width. With nothing set it finds the node itself (the first USB serial device)
and uses a N.O.M.A.D. map server at `http://localhost:8080` (`--nomad-url`, or blank for no map; `--home LAT,LON` says
where the map starts until something on it has a position; `--save` keeps the options). The Pi's own UART pins
(`/dev/serial0`) are never guessed: pick that port in Settings or give `--serial-port`. Messages and nodes are kept in
`~/.config/simplemesh/`, so history survives restarts. As a user service:

    # ~/.config/systemd/user/simplemesh.service
    [Service]
    ExecStart=/path/to/simplemesh
    Restart=on-failure
    [Install]
    WantedBy=default.target

The user needs to be in the `dialout` group to open the serial port.
