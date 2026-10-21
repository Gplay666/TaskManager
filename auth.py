"""Пароли и токены сессий. Только стандартная библиотека.

Пароли хранятся как scrypt-хеш со случайной солью:
    scrypt$N$r$p$<salt hex>$<hash hex>

Токен сессии — случайная строка (в БД попадает только её SHA-256).
Смысл в том, что ни утечка password_hash, ни утечка sessions не даёт
залогиниться по чужому паролю или сессии.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Имя cookie с токеном сессии. Общее для api.py (ставит cookie) и cli.py
# (шлёт токен заголовком).
SESSION_COOKIE = "tm_session"

# scrypt: N=2**14, r=8 => ~16 МиБ памяти на проверку, это осознанно.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

_ALGO = "scrypt"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return f"{_ALGO}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != _ALGO:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Хеш-заглушка: сравнение с ней выравнивает время ответа для несуществующего
# пользователя, чтобы по скорости нельзя было отличить «нет такого логина».
# Считается лениво: CLI импортирует этот модуль и не должен платить за scrypt.
_DUMMY_HASH: str | None = None


def dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("no-such-user")
    return _DUMMY_HASH
