// Quick-reference frequencies for the card beside the title. Plain JSON on purpose, so it is easy to check and edit.
const FREQUENCIES = {
 "checked": "2026-10-28",
 "sources": "ARRL band plan, ARISS and AMSAT ISS pages, WSJT-X and JS8Call defaults, ac6v.com, hwn.org. US (IARU Region 2) frequencies. Band plans vary by region and the ISS radios are switched off for spacewalks and dockings, so check ariss.org/current-status-of-iss-stations before a pass.",
 "groups": [
  {
   "name": "APRS and packet",
   "note": "",
   "items": [
    {
     "f": "144.390",
     "mode": "FM, 1200 baud",
     "what": "APRS, North America"
    },
    {
     "f": "144.800",
     "mode": "FM, 1200 baud",
     "what": "APRS, Europe and Africa"
    },
    {
     "f": "10.1476 USB",
     "mode": "300 baud",
     "what": "APRS on HF, 30 m. The exact dial setting depends on your TNC's tones"
    },
    {
     "f": "145.010 - 145.090",
     "mode": "FM packet",
     "what": "Widely used for packet: 145.01, .03, .05, .07, .09"
    },
    {
     "f": "223.52 - 223.64",
     "mode": "digital",
     "what": "1.25 m digital and packet segment"
    }
   ]
  },
  {
   "name": "ISS and satellites",
   "note": "Ask ariss.org which radio is on before a pass. Listen on the downlink; transmit only when the ISS is above you.",
   "items": [
    {
     "f": "145.990 up / 437.800 down",
     "mode": "FM, 67 Hz tone on the uplink",
     "what": "ISS voice repeater (Columbus, NA1SS)"
    },
    {
     "f": "437.825",
     "mode": "FM, 1200 baud",
     "what": "ISS APRS as ARISS lists it now (Zvezda, RS0ISS)"
    },
    {
     "f": "145.825",
     "mode": "FM, 1200 baud",
     "what": "ISS APRS, the long-standing frequency AMSAT lists. Try it if 437.825 is silent"
    },
    {
     "f": "437.550",
     "mode": "FM, 1200 baud",
     "what": "ISS APRS, UHF alternate, rarely used"
    },
    {
     "f": "145.800",
     "mode": "FM",
     "what": "ISS downlink for crew contacts and SSTV pictures"
    },
    {
     "f": "144.490 up / 145.800 down",
     "mode": "FM",
     "what": "Crew contact in the Americas, rarely used"
    },
    {
     "f": "145.80 - 146.00, 435 - 438",
     "mode": "",
     "what": "Amateur satellite sub-bands"
    }
   ]
  },
  {
   "name": "FM simplex calling",
   "note": "Call on these, then move to another simplex channel to talk.",
   "items": [
    {
     "f": "146.520",
     "mode": "FM",
     "what": "2 m national simplex calling. Simplex also 146.40 - 146.58 and 147.42 - 147.57"
    },
    {
     "f": "446.000",
     "mode": "FM",
     "what": "70 cm national simplex calling"
    },
    {
     "f": "52.525",
     "mode": "FM",
     "what": "6 m national simplex calling; 52.540 is the secondary"
    },
    {
     "f": "223.500",
     "mode": "FM",
     "what": "1.25 m simplex calling; simplex is 223.40 - 223.52"
    },
    {
     "f": "927.500",
     "mode": "FM",
     "what": "33 cm simplex calling (some regions choose another)"
    },
    {
     "f": "1294.500",
     "mode": "FM",
     "what": "23 cm national simplex calling"
    }
   ]
  },
  {
   "name": "Weak signal: SSB, CW and digital",
   "note": "Calling frequencies; move off a few kHz once you have a contact.",
   "items": [
    {
     "f": "50.125 USB",
     "mode": "SSB",
     "what": "6 m calling. DX window is 50.100 - 50.130"
    },
    {
     "f": "144.200 USB",
     "mode": "SSB",
     "what": "2 m national SSB calling. CW and weak signal 144.050 - 144.100"
    },
    {
     "f": "222.100",
     "mode": "CW and SSB",
     "what": "1.25 m calling"
    },
    {
     "f": "432.100",
     "mode": "SSB, CW",
     "what": "70 cm calling. Weak signal 432.07 - 433.00"
    },
    {
     "f": "902.100",
     "mode": "CW and SSB",
     "what": "33 cm weak signal (some regions use 903.100)"
    },
    {
     "f": "1296.100",
     "mode": "CW and SSB",
     "what": "23 cm calling"
    },
    {
     "f": "50.313",
     "mode": "FT8",
     "what": "6 m FT8"
    },
    {
     "f": "50.260",
     "mode": "WSJT meteor scatter",
     "what": "6 m meteor scatter calling"
    },
    {
     "f": "144.174",
     "mode": "FT8",
     "what": "2 m FT8"
    }
   ]
  },
  {
   "name": "HF digital (USB dial frequency, MHz)",
   "note": "Defaults of the software; the users of each mode move around them.",
   "items": [
    {
     "f": "3.573 7.074 10.136 14.074 18.100 21.074 24.915 28.074",
     "mode": "FT8",
     "what": "FT8 on 80, 40, 30, 20, 17, 15, 12 and 10 m. A second set is 7.071, 10.133, 14.071"
    },
    {
     "f": "3.578 7.078 10.130 14.078 18.104 21.078 24.922 28.078",
     "mode": "JS8Call",
     "what": "Keyboard conversations that can relay messages between stations"
    },
    {
     "f": "3.5686 7.0386 10.1387 14.0956 18.1046 21.0946 24.9246 28.1246",
     "mode": "WSPR",
     "what": "Weak-signal beacons; good for checking propagation"
    },
    {
     "f": "3.580 7.080 10.142 14.070 18.100 21.080 24.920 28.120",
     "mode": "PSK31",
     "what": "Keyboard chat; 7.080 is the Region 2 frequency"
    },
    {
     "f": "14.080 - 14.100",
     "mode": "RTTY",
     "what": "20 m RTTY. 21.080 - 21.100 on 15 m, 28.080 - 28.100 on 10 m"
    },
    {
     "f": "3.845 7.171 14.230 21.340 28.680",
     "mode": "SSTV",
     "what": "Slow-scan television pictures"
    }
   ]
  },
  {
   "name": "Nets and weather",
   "note": "",
   "items": [
    {
     "f": "14.325 USB / 7.268 LSB",
     "mode": "SSB",
     "what": "Hurricane Watch Net: 14.325 by day, 7.268 at night, only while a hurricane is active. Stay off while it runs (hwn.org)"
    },
    {
     "f": "162.400 162.425 162.450 162.475 162.500 162.525 162.550",
     "mode": "NFM, receive",
     "what": "NOAA Weather Radio. Receive only"
    }
   ]
  },
  {
   "name": "LoRa mesh (no license)",
   "note": "",
   "items": [
    {
     "f": "906.875",
     "mode": "LoRa",
     "what": "Meshtastic, US default LongFast (slot 20)"
    },
    {
     "f": "910.525",
     "mode": "LoRa, 62.5 kHz, SF7",
     "what": "MeshCore, USA and Canada preset (the one the mesh page uses)"
    }
   ]
  }
 ]
};
