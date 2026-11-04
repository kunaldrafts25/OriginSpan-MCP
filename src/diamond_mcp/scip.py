"""Bounded reader for SCIP occurrence/symbol metadata (no indexer execution).

Consumes documented protobuf wire fields, skipping unknown fields. It does not
implement SCIP diagnostics, signature documents or complete language semantics.
Schema: https://github.com/scip-code/scip/blob/main/scip.proto (Apache-2.0).
"""
from pathlib import PurePosixPath


def varint(data, at):
    result = shift = 0
    for _ in range(10):
        if at >= len(data):
            raise ValueError("Truncated SCIP varint")
        byte = data[at]
        at += 1
        result |= (byte & 127) << shift
        if not byte & 128:
            return result, at
        shift += 7
    raise ValueError("Oversized SCIP varint")


def fields(data):
    at = count = 0
    while at < len(data):
        count += 1
        if count > 200000:
            raise ValueError("SCIP field budget exceeded")
        tag, at = varint(data, at)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid SCIP field")
        if wire == 0:
            value, at = varint(data, at)
        elif wire == 2:
            length, at = varint(data, at)
            if length > len(data) - at:
                raise ValueError("Truncated SCIP message")
            value = data[at:at + length]
            at += length
        elif wire in {1, 5}:
            size = 8 if wire == 1 else 4
            if at + size > len(data):
                raise ValueError("Truncated SCIP fixed field")
            value = data[at:at + size]
            at += size
        else:
            raise ValueError("Unsupported SCIP wire encoding")
        yield number, value


def text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else ""


def read_index(data):
    if len(data) > 2_000_000:
        raise ValueError("SCIP index exceeds 2 MB bounded import limit")
    output = []
    for number, document in fields(data):
        if number != 2 or not isinstance(document, bytes):
            continue
        doc = list(fields(document))
        path = next((text(v) for n, v in doc if n == 1), "")
        pp = PurePosixPath(path)
        if not path or pp.is_absolute() or any(p in {".", ".."} for p in pp.parts) or "\\" in path or ":" in path:
            raise ValueError("Invalid SCIP document path")
        docs = {}
        for n, info in doc:
            if n == 3 and isinstance(info, bytes):
                entries = list(fields(info))
                symbol = next((text(v) for k, v in entries if k == 1), "")
                docs[symbol] = "\n".join(text(v) for k, v in entries if k == 3)
        for n, occurrence in doc:
            if n != 2 or not isinstance(occurrence, bytes):
                continue
            values = list(fields(occurrence))
            symbol = next((text(v) for k, v in values if k == 2), "")
            role = next((v for k, v in values if k == 3 and isinstance(v, int)), 0)
            ranges = [v for k, v in values if k in {8, 9} and isinstance(v, bytes)]
            if ranges:
                line = next((v for k, v in fields(ranges[-1]) if k == 1), 0)
            else:
                packed = next((v for k, v in values if k == 1 and isinstance(v, bytes)), b"")
                line = varint(packed, 0)[0] if packed else next((v for k, v in values if k == 1 and isinstance(v, int)), 0)
            if symbol:
                output.append(dict(symbol=symbol, file=path, line=line + 1, definition=bool(role & 1), documentation=docs.get(symbol, "")))
            if len(output) > 30000:
                raise ValueError("SCIP occurrence budget exceeded")
    return output
