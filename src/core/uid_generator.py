import os
import time
import re

# Crockford's Base32 alphabet (excludes I, L, O, U to prevent visual confusion and offensive words)
CROCKFORD_BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
UID_REGEX = re.compile(r"^\+OA-[0-9A-HJKMNP-Z]{4}-[0-9A-HJKMNP-Z]{4}$")


def encode_base32(value: int, length: int = 8) -> str:
    """Encodes an integer into a fixed-length Crockford Base32 string."""
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD_BASE32_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_agent_uid() -> str:
    """
    Generates a collision-resistant, short Crockford Base32 agent unique identifier (phone number).
    Format: +OA-XXXX-XXXX (e.g. +OA-7K9M-4X2B)
    40 bits of cryptographic random entropy (~1.1 trillion unique combinations).
    """
    rand_val = int.from_bytes(os.urandom(5), "big") & 0xFFFFFFFFFF
    encoded = encode_base32(rand_val, length=8)
    return f"+OA-{encoded[:4]}-{encoded[4:]}"



def is_valid_agent_uid(uid: str) -> bool:
    """Validates if a string matches the +OA-XXXX-XXXX Crockford Base32 format."""
    if not isinstance(uid, str):
        return False
    return bool(UID_REGEX.match(uid.upper()))
