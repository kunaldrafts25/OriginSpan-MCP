import pytest
from diamond_mcp.scip import read_index


def vi(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def blob(n, value):
    value = value.encode() if isinstance(value, str) else value
    return vi((n << 3) | 2) + vi(len(value)) + value


def test_binary_scip_definitions_references_and_typed_ranges():
    symbol = "scip-python pypi fixture 1 model/Contract#"
    occurrence = blob(1, b"\x04\x00\x04") + blob(2, symbol) + vi(3 << 3) + vi(1)
    document = blob(1, "model.py") + blob(2, occurrence)
    index = blob(2, document)
    result = read_index(index)
    assert result == [dict(symbol=symbol, file="model.py", line=5, definition=True, documentation="")]
    typed = blob(8, vi(1 << 3) + vi(10)) + blob(2, symbol)
    result = read_index(blob(2, blob(1, "model.py") + blob(2, typed)))
    assert result[0]["line"] == 11 and not result[0]["definition"]


def test_malformed_scip_and_unsafe_paths_rejected():
    for value in (b"\x12\xff", blob(2, blob(1, "../escape.py")), b"\x00"):
        with pytest.raises(ValueError):
            read_index(value)
