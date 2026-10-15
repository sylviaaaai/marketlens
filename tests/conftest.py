"""Opt-in no-disk-DB/offline audit profile; normal test mode is unchanged."""
import os
import socket
import sqlite3
import sys
import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") != "1":
        return
    for item in items:
        if item.name in ("test_explicit_cli_start_is_search_only_and_preserves_pilot",
                         "test_dpapi_restart_details_without_search_or_plaintext"):
            item.add_marker(pytest.mark.skip(reason="No-disk audit: file fixture/subprocess needs a new SQLite file"))


@pytest.fixture(autouse=True)
def offline_memory_audit(monkeypatch):
    if os.environ.get("MARKETLENS_TEST_MEMORY_ONLY") != "1":
        return
    connect = sqlite3.connect
    def memory_only(database, *args, **kwargs):
        if database != ":memory:":
            pytest.skip("No-disk audit: this legacy test requires file SQLite; not executed")
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", memory_only)
    socket_connect = socket.socket.connect
    def no_network(sock, address):
        # Windows asyncio uses socket.socketpair internally; this is local IPC,
        # not an HTTP request. Do not allow arbitrary loopback/MCP connections.
        caller = sys._getframe(1)
        if caller.f_code.co_name == "socketpair" and caller.f_globals.get("__name__") == "socket":
            return socket_connect(sock, address)
        pytest.fail("Real network forbidden in storage integration audit")
    monkeypatch.setattr(socket.socket, "connect", no_network)
