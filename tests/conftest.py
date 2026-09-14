import socket

import pytest


@pytest.fixture(autouse=True)
def local_network_only(monkeypatch):
    """All tests fail closed before any non-loopback DNS lookup or connection."""
    original = socket.getaddrinfo

    def lookup(host, *args, **kwargs):
        assert host in ("127.0.0.1", "localhost", "::1"), "External network forbidden in tests"
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
