"""
Encodes the chat a file button was shown in (a group, or the user's own PM)
into the deep-link payload used by plugins/search.py's file buttons, so
plugins/start.py's deliver_file can later report *where* a file was taken
from — needed for the delivery-logging feature (see database/filedeliverydb.py).

Telegram restricts /start payloads to [A-Za-z0-9_-]{1,64}, and our file_id
itself already uses that same alphabet (urlsafe base64, padding stripped —
see database/filesdb.py's encode_file_id), so we can't just glue the two
together with an arbitrary separator and split on it later: the file_id may
itself contain "_" or "-". Instead the origin is packed as a signed 64-bit
int (struct '<q') and base64url-encoded — 8 raw bytes always encodes to
exactly 11 base64 chars with no padding, regardless of the value, so the
result has a *fixed* length. That lets deliver_file unambiguously split the
payload by taking the last 11 characters as the origin code and everything
before that (minus the joining "_") as the file_id, no matter what
characters either part happens to contain.
"""

import base64
import struct

ORIGIN_CODE_LEN = 11  # fixed: 8 bytes -> ceil(8/3)*4 = 12, minus 1 padding char stripped = 11

# Sentinel meaning "delivered directly in the bot's own PM" (as opposed to a
# real chat id), since 0 is never a valid Telegram chat id.
PM_ORIGIN = 0


def encode_origin(chat_id: int) -> str:
    packed = struct.pack('<q', chat_id)
    return base64.urlsafe_b64encode(packed).decode().rstrip('=')


def decode_origin(code: str):
    try:
        padded = code + '=' * (-len(code) % 4)
        packed = base64.urlsafe_b64decode(padded.encode())
        return struct.unpack('<q', packed)[0]
    except Exception:
        return None


def build_file_payload(file_id: str, origin_chat_id: int) -> str:
    return f"file_{file_id}_{encode_origin(origin_chat_id)}"


def split_file_payload(payload: str):
    """payload is everything after the leading 'file_' (already stripped by
    the caller). Returns (file_id, origin_chat_id_or_None)."""
    if len(payload) > ORIGIN_CODE_LEN + 1 and payload[-(ORIGIN_CODE_LEN + 1)] == '_':
        file_id = payload[:-(ORIGIN_CODE_LEN + 1)]
        origin = decode_origin(payload[-ORIGIN_CODE_LEN:])
        # Plausibility guard: real Telegram chat/user ids are well inside
        # this range. Without it, an *old* plain (pre-origin) file_id link
        # whose tail happens to decode to *some* int would get silently
        # (and wrongly) truncated, breaking that link. Bounding the range
        # makes that misparse very unlikely for genuine old links, whose
        # tails are effectively random bytes from the packed file struct.
        if origin is not None and 0 < abs(origin) < 10 ** 16:
            return file_id, origin
    # No valid origin suffix found (older/plain links without one) — treat
    # the whole thing as just the file_id, origin unknown.
    return payload, None
