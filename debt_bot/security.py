from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def verify_discord_request(public_key: str, signature: str, timestamp: str, body: bytes) -> bool:
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode("utf-8") + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False
