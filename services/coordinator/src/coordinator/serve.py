"""Production TLS endpoint; ALB encrypts the target hop and SG restricts callers."""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    tls = os.environ.get("COORDINATOR_TLS", "false").lower() in {"1", "true"}
    with tempfile.TemporaryDirectory(prefix="coordinator-tls-") as directory:
        options = {}
        if tls:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "coordinator.internal")]
            )
            now = datetime.now(UTC)
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=30))
                .sign(key, hashes.SHA256())
            )
            private = Path(directory) / "key.pem"
            public = Path(directory) / "cert.pem"
            private.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            private.chmod(0o600)
            public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            options = {"ssl_keyfile": str(private), "ssl_certfile": str(public)}
        uvicorn.run(
            "coordinator.main:app",
            host="0.0.0.0",
            port=int(os.environ.get("API_PORT", "8000")),
            workers=1,
            **options,
        )


if __name__ == "__main__":
    main()
