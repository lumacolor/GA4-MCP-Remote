"""Upstream field-name errors must reach the client.

The Data API answers an unknown field with actionable text -- "Did you mean
purchaserRate? Field purchases is not a valid metric." plus a link to the schema.
Collapsing that into "Internal server error" is expensive: in the 04.09.2026 field
test a model read an invalid dimension name as a data problem and told the customer
their tracking was broken. The valid names are published, so there is nothing
internal to protect here.
"""

from __future__ import annotations

from google.api_core import exceptions as google_exceptions

from ga4_remote_mcp.errors.normalize import INTERNAL_ERROR_MESSAGE, map_exception_to_code


def test_invalid_argument_keeps_upstream_guidance() -> None:
    detail = (
        "Did you mean purchaserRate? Field purchases is not a valid metric. "
        "For a list of valid dimensions and metrics, see "
        "https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema"
    )
    code, message = map_exception_to_code(google_exceptions.InvalidArgument(detail))
    assert code == "invalid_argument"
    assert "purchaserRate" in message
    assert message != INTERNAL_ERROR_MESSAGE


def test_unknown_exceptions_stay_generic() -> None:
    """Only InvalidArgument is opened up; everything else keeps its lid on."""
    code, message = map_exception_to_code(RuntimeError("db://user:pw@internal-host/x"))
    assert code == "internal_error"
    assert message == INTERNAL_ERROR_MESSAGE
