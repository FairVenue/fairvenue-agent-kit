"""Lazy, exact-pinned primitives. These authenticate bytes, not public authority."""

from __future__ import annotations

from importlib.metadata import version

from .protocol import AgentError, encoded_hex, fail, hex_value


class CryptoPrimitives:
    def __init__(self):
        try:
            if any(version(name) != expected for name, expected in (
                ("PyNaCl", "1.6.2"), ("pycryptodome", "3.24.0"),
                ("cffi", "2.1.1"), ("pycparser", "3.0"),
            )):
                fail("crypto_unavailable")
            from Crypto.Hash import keccak
            from nacl.bindings import crypto_core_ed25519_is_valid_point
            from nacl.signing import VerifyKey

            self._keccak = keccak
            self._valid = crypto_core_ed25519_is_valid_point
            self._verify_key = VerifyKey
            if self.digest(b"") != "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470":
                fail("crypto_unavailable")
        except Exception:
            raise AgentError("crypto_unavailable") from None

    def digest(self, data):
        if type(data) is not bytes or len(data) > 16384:
            fail()
        return encoded_hex(self._keccak.new(digest_bits=256, data=data).digest())

    def strong(self, public_key):
        try:
            # libsodium checks canonical encoding, curve/subgroup membership and non-small-order.
            return self._valid(bytes.fromhex(hex_value(public_key)[2:])) is True
        except Exception:
            return False

    def verify(self, signature, message, public_key):
        try:
            raw = bytes.fromhex(hex_value(signature, 64, nonzero=False)[2:])
            if (not self.strong(public_key) or not self.strong(encoded_hex(raw[:32]))
                    or int.from_bytes(raw[32:], "little") >= (1 << 252) + 27742317777372353535851937790883648493
                    or type(message) is not bytes or len(message) > 8192):
                return False
            self._verify_key(bytes.fromhex(public_key[2:])).verify(message, raw)
            return True
        except Exception:
            return False


class MemorySigner:
    """Explicit isolated signer. No persistence, export, HTTP or logging of secrets."""

    def __init__(self):
        primitives = CryptoPrimitives()
        from nacl.signing import SigningKey

        self.__key = SigningKey.generate()
        self.public_key = encoded_hex(bytes(self.__key.verify_key))
        if not primitives.strong(self.public_key):
            fail("signature_invalid")

    @classmethod
    def _from_v1_seed(cls, seed):
        primitives = CryptoPrimitives()
        if type(seed) is not bytes or len(seed) != 32:
            fail()
        from nacl.signing import SigningKey

        signer = cls.__new__(cls)
        signer.__key = SigningKey(seed)
        signer.public_key = encoded_hex(bytes(signer.__key.verify_key))
        if not primitives.strong(signer.public_key):
            fail("signature_invalid")
        return signer

    async def sign_bytes(self, message):
        if type(message) is not bytes or len(message) > 8192:
            fail()
        return bytes(self.__key.sign(message).signature)

    def __repr__(self):
        return "<schema6 MemorySigner: secret not exported>"
