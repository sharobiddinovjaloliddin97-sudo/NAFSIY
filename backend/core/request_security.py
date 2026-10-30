import ipaddress

from fastapi import HTTPException, Request

from backend.core.config import settings


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def require_trusted_origin(request: Request, *, bootstrap: bool = False):
    if request.method not in UNSAFE_METHODS and not bootstrap:
        return
    origins = request.headers.getlist("origin")
    if len(origins) > 1:
        raise HTTPException(403, "Untrusted request origin", headers={"Cache-Control": "no-store"})
    origin = origins[0] if origins else None
    if origin and origin not in settings.AUTH_TRUSTED_ORIGINS:
        raise HTTPException(403, "Untrusted request origin", headers={"Cache-Control": "no-store"})
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Untrusted request origin", headers={"Cache-Control": "no-store"})


def parsed_ip(value: str):
    address = ipaddress.ip_address(value.strip())
    return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address


def client_ip(request: Request) -> str:
    try:
        peer = parsed_ip(request.client.host if request.client else "")
    except ValueError:
        return "unknown"
    networks = [ipaddress.ip_network(value) for value in settings.TRUSTED_PROXY_CIDRS]

    def trusted(address):
        return any(address in network for network in networks)

    if not trusted(peer):
        return str(peer)
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    if not forwarded or len(forwarded) > 1024:
        return str(peer)
    parts = forwarded.split(",")
    if len(parts) > 10:
        return str(peer)
    address = peer
    for value in reversed(parts):
        if not trusted(address):
            return str(address)
        try:
            address = parsed_ip(value)
        except ValueError:
            return str(peer)
    return str(address)


def rate_ip(request: Request) -> str:
    value = client_ip(request)
    if ":" in value:
        return str(ipaddress.ip_network(value + "/64", strict=False))
    return value
