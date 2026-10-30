"""A self-signed certificate for a LAN page: enough to encrypt the connection and make the page a "secure context",
which browsers insist on before they allow notifications and service workers. It does not prove who the server is, so
a browser warns the first time and asks to continue; nothing else here (or in the client side) checks it either.

The certificate is made once with the openssl command line tool and kept next to the settings; delete it to make a new one.
"""
from __future__ import annotations

import socket
import ssl
import subprocess
from pathlib import Path

DAYS = 825  # what Apple's browsers still accept for a certificate that is trusted by hand


def _names() -> tuple[list[str], list[str]]:
    """The names and addresses this machine is reached by, for the certificate to list."""
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
    return list(dict.fromkeys(dns)), list(dict.fromkeys(ips))


def _fresh(cert: Path) -> bool:
    """Is the certificate there, and good for another month?"""
    if not cert.is_file():
        return False
    try:
        return subprocess.run(["openssl", "x509", "-checkend", str(30 * 86400), "-noout", "-in", str(cert)],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: keep what there is


def ensure(folder: Path, name: str = "simpleaprs") -> tuple[Path, Path] | None:
    """The certificate and key files in folder, made if they are missing or about to run out (name is what a device calls
    the certificate when it is installed). None if openssl can't make them."""
    cert, key = folder / "cert.pem", folder / "key.pem"
    if key.is_file() and _fresh(cert):
        return cert, key
    dns, ips = _names()
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


def context(folder: Path, name: str = "simpleaprs") -> ssl.SSLContext | None:
    """A server's TLS settings from the certificate in folder, or None if there isn't one to be had."""
    files = ensure(folder, name)
    if not files:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(*map(str, files))
    return ctx
