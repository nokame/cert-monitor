import os
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def generate_key(key_size=2048):
    return rsa.generate_private_key(public_exponent=65537, key_size=key_size)


def load_or_create_key(path):
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return serialization.load_pem_private_key(fh.read(), password=None)
    key = generate_key()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    os.chmod(path, 0o600)
    return key


def key_to_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def make_csr(key, domains) -> bytes:
    primary = domains[0]
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, primary)])
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),
        critical=False,
    )
    csr = builder.sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.DER)


def parse_chain(chain_pem: bytes):
    certs = x509.load_pem_x509_certificates(chain_pem)
    return certs[0], certs[1:]


def build_pkcs12(chain_pem: bytes, key, password: str) -> bytes:
    leaf, cas = parse_chain(chain_pem)
    return pkcs12.serialize_key_and_certificates(
        name=domains_from_cert(leaf)[0].encode(),
        key=key,
        cert=leaf,
        cas=cas or None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )


def domains_from_cert(cert):
    cn = ""
    for attr in cert.subject:
        if attr.oid == NameOID.COMMON_NAME:
            cn = attr.value
    sans = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass
    return [cn] + [s for s in sans if s != cn]


def cert_not_after(cert) -> datetime:
    return cert.not_valid_after_utc


def self_signed_test_cert(key, domains, days=90, issuer_key=None, issuer_cert=None):
    subject = issuer_cert.subject if issuer_cert else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    builder = x509.CertificateBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    ).issuer_name(
        subject
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=days)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),
        critical=False,
    )
    return builder.sign(issuer_key or key, hashes.SHA256())
