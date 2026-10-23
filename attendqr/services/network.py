"""Which client addresses may open a QR scan link (SCAN_ALLOWED_NETWORKS).

The setting is a comma-separated list of IP ranges in CIDR form, or the word
"any". When it is empty, only private (LAN) addresses are accepted, which
matches a LAN deployment and fails closed on a public one. Anything malformed
stops the app from starting rather than being skipped.
"""
import ipaddress

from flask import current_app, request

ALLOW_ANY = 'any'

# Used when SCAN_ALLOWED_NETWORKS is empty: phones on the same private network.
# Loopback is left out on purpose. Behind a reverse proxy on the same machine,
# with TRUSTED_PROXY_COUNT unset, every request appears to come from 127.0.0.1,
# and allowing that would let every scan through.
PRIVATE_NETWORKS = (
    '10.0.0.0/8',
    '172.16.0.0/12',
    '192.168.0.0/16',
    '169.254.0.0/16',
    'fc00::/7',
    'fe80::/10',
)

# Headers a reverse proxy adds. Seeing one when no proxy is trusted means
# remote_addr is the proxy's address, not the student's.
PROXY_HEADERS = ('X-Forwarded-For', 'X-Real-IP', 'Forwarded')


class ScanNetworkConfigError(ValueError):
    pass


def parse_scan_networks(raw):
    """Return None for "any", otherwise a tuple of networks.

    Raises ScanNetworkConfigError for anything that is not a valid range, so a
    typo cannot quietly widen or disable the check.
    """
    if isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw]
    else:
        items = [item.strip() for item in str(raw or '').split(',')]
    items = [item for item in items if item]

    if not items:
        return tuple(ipaddress.ip_network(net) for net in PRIVATE_NETWORKS)

    if any(item.lower() == ALLOW_ANY for item in items):
        if len(items) > 1:
            raise ScanNetworkConfigError(
                f"SCAN_ALLOWED_NETWORKS mixes '{ALLOW_ANY}' with IP ranges ({', '.join(items)}). "
                f"Use either '{ALLOW_ANY}' or a list of ranges."
            )
        return None

    networks = []
    for item in items:
        try:
            network = ipaddress.ip_network(item, strict=True)
        except ValueError as exc:
            hint = ''
            try:
                hint = f" Did you mean {ipaddress.ip_network(item, strict=False)}?"
            except ValueError:
                pass
            raise ScanNetworkConfigError(f"SCAN_ALLOWED_NETWORKS has an invalid entry '{item}': {exc}.{hint}") from None
        if network.prefixlen == 0:
            raise ScanNetworkConfigError(
                f"SCAN_ALLOWED_NETWORKS entry '{item}' matches every address. "
                f"Write '{ALLOW_ANY}' if you mean to accept scans from anywhere."
            )
        networks.append(network)
    return tuple(networks)


def describe_scan_networks(networks):
    if networks is None:
        return 'any address (SCAN_ALLOWED_NETWORKS=any)'
    return ', '.join(str(net) for net in networks)


def _client_address(value):
    try:
        address = ipaddress.ip_address((value or '').split('%', 1)[0])
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def scan_network_error():
    """None if this request may open a scan link, otherwise a message for the log.

    Fails closed: an unparseable client address, or proxy headers that the app
    has not been told to trust, are refused.
    """
    networks = parse_scan_networks(current_app.config['SCAN_ALLOWED_NETWORKS'])
    if networks is None:
        return None

    if not current_app.config['TRUSTED_PROXY_COUNT']:
        header = next((name for name in PROXY_HEADERS if name in request.headers), None)
        if header:
            return (
                f"request has a {header} header but TRUSTED_PROXY_COUNT is 0, so the client address "
                f"({request.remote_addr}) is probably the proxy's. Set TRUSTED_PROXY_COUNT."
            )

    address = _client_address(request.remote_addr)
    if address is None:
        return f"client address {request.remote_addr!r} is not a valid IP"
    if not any(address in net for net in networks if net.version == address.version):
        return f"{address} is not in SCAN_ALLOWED_NETWORKS"
    return None
