import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from monitor.acme import certs, jws
from monitor.acme.client import AcmeClient
from monitor.acme.challenges import WebrootHandler

DOMAIN = "test.example.com"
state = {"nonces": set(), "served_tokens": {}}


def make_test_ca():
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")]))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return ca_key, ca_cert


def issue_leaf_from_csr(ca_key, ca_cert, csr_der):
    csr = x509.load_der_x509_csr(csr_der)
    cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    try:
        sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = [cn]
    leaf = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in sans]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)


class MockAcmeHandler(BaseHTTPRequestHandler):
    def _nonce(self):
        nonce = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        state["nonces"].add(nonce)
        return nonce

    def _send(self, code, body=None, headers=None):
        self.send_response(code)
        self.send_header("Replay-Nonce", self._nonce())
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        payload = (json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b""))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_HEAD(self):
        if self.path == "/new-nonce":
            self.send_response(204)
            self.send_header("Replay-Nonce", self._nonce())
            self.end_headers()
        else:
            self._send(404, {"error": "notfound"})

    def do_GET(self):
        if self.path == "/directory":
            self._send(200, {
                "newNonce": f"http://{self.headers['Host']}/new-nonce",
                "newAccount": f"http://{self.headers['Host']}/new-account",
                "newOrder": f"http://{self.headers['Host']}/new-order",
            })
        elif self.path == "/authz/1":
            token = state["served_tokens"].get("token", "unknown")
            status = "valid" if state.get("challenge_validated") else "pending"
            self._send(200, {
                "status": status,
                "identifier": {"type": "dns", "value": DOMAIN},
                "challenges": [{
                    "type": "http-01", "url": f"http://{self.headers['Host']}/chal/1",
                    "token": token, "status": status,
                }],
            })
        elif self.path == "/order/1":
            self._send(200, {
                "status": "valid" if state.get("challenge_validated") else "pending",
                "certificate": f"http://{self.headers['Host']}/cert/1",
                "finalize": f"http://{self.headers['Host']}/finalize/1",
            })
        elif self.path == "/cert/1":
            self._send(200, state["chain"])
        else:
            self._send(404, {"error": "notfound"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        host = self.headers["Host"]
        if self.path in ("/authz/1", "/order/1", "/cert/1"):
            self.do_GET()
        elif self.path == "/new-account":
            self._send(201, {"status": "valid"}, {"Location": f"http://{host}/acct/1"})
        elif self.path == "/new-order":
            self._send(201, {
                "status": "pending",
                "authorizations": [f"http://{host}/authz/1"],
                "finalize": f"http://{host}/finalize/1",
            }, {"Location": f"http://{host}/order/1"})
        elif self.path == "/chal/1":
            state["challenge_validated"] = True
            self._send(200, {"type": "http-01", "url": f"http://{host}/chal/1", "status": "valid"})
        elif self.path == "/finalize/1":
            payload = json.loads(base64.urlsafe_b64decode(body["payload"] + "=="))
            csr_der = base64.urlsafe_b64decode(payload["csr"] + "==")
            state["chain"] = issue_leaf_from_csr(state["ca_key"], state["ca_cert"], csr_der)
            self._send(200, {
                "status": "valid",
                "certificate": f"http://{host}/cert/1",
                "finalize": f"http://{host}/finalize/1",
            })
        else:
            self._send(404, {"error": "notfound"})

    def log_message(self, *args):
        pass


def main():
    ca_key, ca_cert = make_test_ca()
    state["ca_key"] = ca_key
    state["ca_cert"] = ca_cert
    state["chain"] = None
    state["challenge_validated"] = False

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockAcmeHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    directory_url = f"http://127.0.0.1:{port}/directory"

    tmp = tempfile.mkdtemp()
    account_key_path = os.path.join(tmp, "account.key")
    account_key = certs.load_or_create_key(account_key_path)

    client = AcmeClient(directory_url, account_key, email="test@example.com", timeout=10)

    webroot = os.path.join(tmp, "wwwroot")

    def handler_factory():
        return WebrootHandler(webroot)

    chain_pem, key_pem = client.obtain_certificate([DOMAIN], handler_factory)

    assert client.kid == f"http://127.0.0.1:{port}/acct/1", client.kid
    print("OK  compte ACME créé et kid persisté :", client.kid)

    assert state["challenge_validated"], "le défi n'a pas été validé"
    print("OK  défi http-01 validé via webroot")

    leaf_new, cas = certs.parse_chain(chain_pem)
    assert leaf_new.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DOMAIN
    assert cas, "chaîne incomplète"
    print("OK  chaîne reçue : feuille +", len(cas), "CA")

    order_key = serialization.load_pem_private_key(key_pem, password=None)
    pfx = certs.build_pkcs12(chain_pem, order_key, "pwd123")
    loaded = pkcs12.load_key_and_certificates(pfx, b"pwd123")
    assert loaded[1].subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DOMAIN
    print("OK  PFX généré et rechargé (clé + feuille + CA)")

    kid_path = account_key_path + ".kid"
    with open(kid_path, "w") as fh:
        fh.write(client.kid)
    from monitor.renewals import get_or_create_account

    key2, kid2, _ = get_or_create_account({
        "acme_account_key_path": account_key_path,
    })
    assert kid2 == client.kid
    print("OK  rechargement compte ACME (clé + kid)")

    protected = {"alg": "RS256", "nonce": "n", "url": "u", "jwk": jws.jwk(account_key)}
    signed = jws.sign(account_key, protected, {"hello": "world"})
    signing_input = f"{signed['protected']}.{signed['payload']}".encode()
    account_key.public_key().verify(
        base64.urlsafe_b64decode(signed["signature"] + "=="),
        signing_input,
        __import__("cryptography.hazmat.primitives.asymmetric.padding", fromlist=["padding"]).PKCS1v15(),
        hashes.SHA256(),
    )
    print("OK  signature JWS RS256 vérifiée avec la clé publique")

    server.shutdown()
    print("\nTOUS LES TESTS ACME PASSENT ✅")


if __name__ == "__main__":
    main()
