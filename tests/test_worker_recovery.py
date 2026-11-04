import threading
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from diamond_mcp.pipeline import Pipeline


def test_worker_recovers_catalog_outage_without_logging_credentials(caplog):
    stop = threading.Event()
    calls = []
    closed = []
    def step(*args):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("secret-sql", {}, Exception("secret-password"))
        stop.set()
        return True
    fake = SimpleNamespace(step=step, close_parser=lambda: closed.append(True))
    Pipeline.run(fake, "worker", stop=stop)
    assert len(calls) == 2 and closed == [True]
    assert "worker_catalog_unavailable" in caplog.text
    assert "secret" not in caplog.text


def test_worker_does_not_hide_programming_errors():
    def broken(*args):
        raise RuntimeError("bug")
    closed = []
    fake = SimpleNamespace(step=broken, close_parser=lambda: closed.append(True))
    with pytest.raises(RuntimeError, match="bug"):
        Pipeline.run(fake, "worker")
    assert closed == [True]
