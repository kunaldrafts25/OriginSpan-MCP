import json
import pytest
from sqlalchemy.exc import OperationalError
from diamond_mcp.hosted import startup_diagnostic


@pytest.mark.parametrize("message,code", [
    ("password authentication failed", "DATABASE_AUTH_FAILED"),
    ("could not translate host name", "DATABASE_DNS_FAILED"),
    ("failed to resolve host", "DATABASE_DNS_FAILED"),
    ("connection refused", "DATABASE_CONNECTION_REFUSED"),
    ("connection timeout expired", "DATABASE_CONNECTION_TIMEOUT"),
    ("SSL certificate verify failed", "DATABASE_TLS_FAILED"),
    ("database example does not exist", "DATABASE_NOT_FOUND"),
    ("permission denied", "DATABASE_PERMISSION_DENIED"),
    ("unrecognized error", "DATABASE_OPERATION_FAILED")])
def test_database_errors_have_safe_actionable_codes(message, code):
    secret = "private-password-do-not-log"
    error = OperationalError("SELECT confidential_source", {"token":secret},
                             Exception(message + " " + secret + " postgresql://private-url"))
    report = startup_diagnostic(error, "database_catalog")
    assert report["code"] == code
    serialized = json.dumps(report)
    assert secret not in serialized and "private-url" not in serialized and "confidential_source" not in serialized
    assert report["stage"] == "database_catalog"


def test_state_error_is_not_misreported_as_oidc():
    report = startup_diagnostic(PermissionError("secret-path"), "state_directory")
    assert report["code"] == "STATE_DIRECTORY_UNWRITABLE"
    assert "secret-path" not in json.dumps(report)
