"""Consistent result envelopes returned by Penzer browser tools."""

from typing import Any, Dict


def success(data: Any = None, message: str = "") -> Dict:
    return {
        "status": "success",
        "message": message or "OK",
        "data": data if data is not None else {},
    }


def error(message: str, data: Any = None) -> Dict:
    return {
        "status": "error",
        "message": message,
        "data": data if data is not None else {},
    }


def warning(message: str, data: Any = None) -> Dict:
    return {
        "status": "warning",
        "message": message,
        "data": data if data is not None else {},
    }