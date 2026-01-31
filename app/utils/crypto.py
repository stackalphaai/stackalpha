import hashlib
import secrets


def generate_random_string(length: int = 32) -> str:
    return secrets.token_hex(length // 2)


def generate_random_code(length: int = 8) -> str:
    return secrets.token_hex(length // 2).upper()


def hash_string(value: str, algorithm: str = "sha256") -> str:
    hasher = hashlib.new(algorithm)
    hasher.update(value.encode("utf-8"))
    return hasher.hexdigest()


def secure_compare(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


def generate_api_key() -> str:
    return f"ht_{secrets.token_urlsafe(32)}"
