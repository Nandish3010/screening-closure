"""Public JWKS for an RSA private key (PEM), on stdout. Used by up.sh; needs `cryptography` (a dependency of PyJWT[crypto])."""
import base64
import json
import sys

from cryptography.hazmat.primitives import serialization

KID = "sc-m76-1"


def b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


with open(sys.argv[1], "rb") as fh:
    pub = serialization.load_pem_private_key(fh.read(), password=None).public_key().public_numbers()
print(json.dumps({"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID, "n": b64u(pub.n), "e": b64u(pub.e)}]}))
