"""Publishes hermes.local -> this computer's current LAN IPv4 over mDNS (python-zeroconf), for Windows
and Linux; macOS uses bin/hermes-mdns (dns-sd). Re-registers when the address changes."""
import os
import socket
import time
from datetime import datetime

from zeroconf import IPVersion, ServiceInfo, Zeroconf

NAME = os.environ.get("HERMES_MDNS_NAME", "hermes.local").rstrip(".") + "."


def lan_ip() -> str:
    """The address the default route leaves from (a UDP connect sends nothing)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""
    finally:
        s.close()


def main() -> None:
    zc, info, ip = None, None, ""
    try:
        while True:
            new = lan_ip()
            if new != ip:
                if zc:
                    zc.unregister_all_services()
                    zc.close()
                    zc = None
                ip = new
                if ip:
                    zc = Zeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
                    info = ServiceInfo("_https._tcp.local.", "Hermes._https._tcp.local.", port=443,
                                       properties={"path": "/"}, server=NAME, addresses=[socket.inet_aton(ip)])
                    zc.register_service(info, allow_name_change=True)
                    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} published {NAME.rstrip('.')} -> {ip}", flush=True)
            time.sleep(30)
    finally:
        if zc:
            zc.unregister_all_services()
            zc.close()


if __name__ == "__main__":
    main()
