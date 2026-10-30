"""A self-signed certificate for a LAN page: enough to encrypt the connection and make the page a "secure context",
which browsers insist on before they allow notifications and service workers. It does not prove who the server is, so
a browser warns the first time and asks to continue; nothing else here (or in the client side) checks it either.

The certificate is made once with the openssl command line tool and kept next to the settings; delete it to make a new one.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import subprocess
from pathlib import Path

DAYS = 825  # what Apple's browsers still accept for a certificate that is trusted by hand


def _split(extra) -> tuple[list[str], list[str]]:
    """Names and addresses asked for by hand (as text), told apart: (names, addresses)."""
    dns, ips = [], []
    for item in extra:
        item = str(item).strip()
        try:
            ips.append(str(ipaddress.ip_address(item)))
        except ValueError:
            if item:
                dns.append(item)
    return dns, ips


def _names(extra=()) -> tuple[list[str], list[str]]:
    """The names and addresses this machine is reached by, for the certificate to list, and any asked for by hand (an
    address it will have on another network, say, that it doesn't have right now)."""
    host = socket.gethostname()
    dns = ["localhost", host, f"{host}.local"]
    ips = ["127.0.0.1"]
    try:
        ips += [a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)]
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:  # the address a LAN machine would use (no packet is sent)
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    more_dns, more_ips = _split(extra)
    return list(dict.fromkeys(dns + more_dns)), list(dict.fromkeys(ips + more_ips))


def _fresh(cert: Path) -> bool:
    """Is the certificate there, and good for another month?"""
    if not cert.is_file():
        return False
    try:
        return subprocess.run(["openssl", "x509", "-checkend", str(30 * 86400), "-noout", "-in", str(cert)],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: keep what there is


def _lists(cert: Path, extra) -> bool:
    """Does the certificate already name everything in extra?"""
    dns, ips = _split(extra)
    if not (dns or ips):
        return True
    try:
        out = subprocess.run(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert)],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    have = {part.strip().split(":", 1)[1].lower() for part in out.replace("IP Address:", "IP:").split(",") if ":" in part}
    return all(n.lower() in have for n in dns + ips)


def ensure(folder: Path, name: str = "simpleaprs", extra=()) -> tuple[Path, Path] | None:
    """The certificate and key files in folder, made if they are missing, about to run out, or missing a name in extra
    (name is what a device calls the certificate when it is installed; extra are names or addresses to list as well, which
    makes a new certificate to install on devices again). None if openssl can't make them."""
    cert, key = folder / "cert.pem", folder / "key.pem"
    if key.is_file() and _fresh(cert) and _lists(cert, extra):
        return cert, key
    dns, ips = _names(extra)
    san = ",".join([f"DNS:{n}" for n in dns] + [f"IP:{i}" for i in ips])
    try:
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", str(DAYS), "-subj", f"/CN={name}",
                        "-addext", f"subjectAltName={san}", "-addext", "basicConstraints=critical,CA:TRUE"],
                       capture_output=True, check=True, timeout=60)
        key.chmod(0o600)
    except (OSError, subprocess.SubprocessError):
        return None
    return cert, key


def context(folder: Path, name: str = "simpleaprs", extra=()) -> ssl.SSLContext | None:
    """A server's TLS settings from the certificate in folder, or None if there isn't one to be had."""
    files = ensure(folder, name, extra)
    if not files:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(*map(str, files))
    return ctx
