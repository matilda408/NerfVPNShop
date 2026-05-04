from base64 import b64encode
from functools import lru_cache
from typing import Final
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from src.core.constants import API_V1

HAPP_LINK_PREFIX: Final[str] = "happ://crypt4/"
HAPP_REDIRECT_PATH: Final[str] = f"{API_V1}/happ/connect"

_HAPP_PUBLIC_KEY_V4: Final[bytes] = b"""
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA3UZ0M3L4K+WjM3vkbQnz
ozHg/cRbEXvQ6i4A8RVN4OM3rK9kU01FdjyoIgywve8OEKsFnVwERZAQZ1Trv60B
hmaM76QQEE+EUlIOL9EpwKWGtTL5lYC1sT9XJMNP3/CI0gP5wwQI88cY/xedpOEB
W72EmOOShHUm/b/3m+HPmqwc4ugKj5zWV5SyiT829aFA5DxSjmIIFBAms7DafmSq
LFTYIQL5cShDY2u+/sqyAw9yZIOoqW2TFIgIHhLPWek/ocDU7zyOrlu1E0SmcQQb
LFqHq02fsnH6IcqTv3N5Adb/CkZDDQ6HvQVBmqbKZKf7ZdXkqsc/Zw27xhG7OfXC
tUmWsiL7zA+KoTd3avyOh93Q9ju4UQsHthL3Gs4vECYOCS9dsXXSHEY/1ngU/hjO
WFF8QEE/rYV6nA4PTyUvo5RsctSQL/9DJX7XNh3zngvif8LsCN2MPvx6X+zLouBX
zgBkQ9DFfZAGLWf9TR7KVjZC/3NsuUCDoAOcpmN8pENBbeB0puiKMMWSvll36+2M
YR1Xs0MgT8Y9TwhE2+TnnTJOhzmHi/BxiUlY/w2E0s4ax9GHAmX0wyF4zeV7kDkc
vHuEdc0d7vDmdw0oqCqWj0Xwq86HfORu6tm1A8uRATjb4SzjTKclKuoElVAVa5Jo
oh/uZMozC65SmDw+N5p6Su8CAwEAAQ==
-----END PUBLIC KEY-----
"""


@lru_cache
def _get_happ_public_key() -> RSAPublicKey:
    public_key = serialization.load_pem_public_key(_HAPP_PUBLIC_KEY_V4)

    if not isinstance(public_key, RSAPublicKey):
        raise ValueError(f"Expected RSAPublicKey, got {type(public_key).__name__}")

    return public_key


def make_happ_link(subscription_page_url: str) -> str:
    encrypted = _get_happ_public_key().encrypt(
        subscription_page_url.encode("utf-8"),
        padding.PKCS1v15(),
    )
    encrypted_base64 = b64encode(encrypted).decode("ascii")
    return f"{HAPP_LINK_PREFIX}{encrypted_base64}"


def make_happ_redirect_url(app_domain: str, subscription_page_url: str) -> str:
    happ_link = make_happ_link(subscription_page_url)
    query = urlencode({"url": happ_link})
    return f"https://{app_domain}{HAPP_REDIRECT_PATH}?{query}"
