import json
import time
import urllib.error
import urllib.request

from . import certs, jws


class AcmeError(Exception):
    pass


class AcmeClient:
    def __init__(self, directory_url, account_key, email=None, timeout=20):
        self.directory_url = directory_url
        self.account_key = account_key
        self.email = email
        self.timeout = timeout
        self._kid = None
        self._directory = None

    def _http(self, method, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise AcmeError(f"Erreur réseau vers {url} : {exc}") from exc

    @property
    def directory(self):
        if self._directory is None:
            status, _, body = self._http("GET", self.directory_url)
            if status != 200:
                raise AcmeError(f"Répertoire ACME inaccessible ({status}) : {self.directory_url}")
            self._directory = json.loads(body)
        return self._directory

    def _get_nonce(self):
        status, headers, _ = self._http("HEAD", self.directory["newNonce"])
        nonce = headers.get("Replay-Nonce")
        if not nonce:
            raise AcmeError("Aucun nonce reçu du serveur ACME.")
        return nonce

    def _post(self, url, payload=None):
        protected = {
            "alg": "RS256",
            "nonce": self._get_nonce(),
            "url": url,
        }
        if self._kid:
            protected["kid"] = self._kid
        else:
            protected["jwk"] = jws.jwk(self.account_key)
        body = jws.sign(self.account_key, protected, payload)
        status, headers, raw = self._http(
            "POST", url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/jose+json"},
        )
        return status, headers, raw

    def ensure_account(self):
        if self._kid:
            return self._kid
        payload = {"termsOfServiceAgreed": True, "onlyReturnExisting": False}
        if self.email:
            payload["contact"] = [f"mailto:{self.email}"]
        status, headers, raw = self._post(self.directory["newAccount"], payload)
        kid = headers.get("Location")
        if status not in (200, 201) or not kid:
            raise AcmeError(f"Création de compte ACME échouée ({status}) : {raw[:300].decode(errors='replace')}")
        self._kid = kid
        return kid

    def set_kid(self, kid):
        self._kid = kid

    @property
    def kid(self):
        return self._kid

    def _post_as_get_json(self, url):
        status, _, raw = self._post(url, None)
        if status >= 400:
            raise AcmeError(f"Requête échouée ({status}) sur {url} : {raw[:300].decode(errors='replace')}")
        return json.loads(raw) if raw else {}

    def new_order(self, domains):
        payload = {"identifiers": [{"type": "dns", "value": d} for d in domains]}
        status, headers, raw = self._post(self.directory["newOrder"], payload)
        if status != 201:
            raise AcmeError(f"Création de commande échouée ({status}) : {raw[:300].decode(errors='replace')}")
        order = json.loads(raw)
        order_url = headers.get("Location")
        if order_url:
            order["order_url"] = order_url
        return order

    def _poll_until(self, url, ok_statuses, fail_statuses, timeout, interval=2):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self._post_as_get_json(url)
            status = last.get("status")
            if status in ok_statuses:
                return last
            if status in fail_statuses:
                problem = last.get("error") or last.get("challenges", [{}])
                raise AcmeError(f"Statut ACME « {status} » sur {url} : {json.dumps(problem)[:300]}")
            time.sleep(interval)
        raise AcmeError(f"Délai dépassé en attendant « {'/'.join(ok_statuses)} » sur {url}")

    def obtain_certificate(self, domains, handler_factory, poll_timeout=120):
        self.ensure_account()
        order = self.new_order(domains)

        for authz_url in order.get("authorizations", []):
            authz = self._post_as_get_json(authz_url)
            if authz.get("status") != "pending":
                continue
            challenges = authz.get("challenges", [])
            challenge = next((c for c in challenges if c.get("type") == "http-01"), None)
            if challenge is None:
                raise AcmeError("Aucun défi http-01 proposé par le serveur ACME.")
            token = challenge["token"]
            key_auth = f"{token}.{jws.thumbprint(self.account_key)}"
            with handler_factory() as handler:
                handler.serve(token, key_auth)
                status, _, raw = self._post(challenge["url"], {})
                if status >= 400:
                    raise AcmeError(f"Notification de défi échouée ({status}) : {raw[:300].decode(errors='replace')}")
            self._poll_until(authz_url, {"valid"}, {"invalid", "deactivated", "revoked"}, poll_timeout)

        order_key = certs.generate_key()
        csr = certs.make_csr(order_key, domains)
        finalize_url = order["finalize"]
        status, _, raw = self._post(finalize_url, {"csr": jws.b64url(csr)})
        if status >= 400:
            raise AcmeError(f"Finalisation échouée ({status}) : {raw[:300].decode(errors='replace')}")
        order = self._poll_until(order["order_url"], {"valid"}, {"invalid"}, poll_timeout)
        cert_url = order.get("certificate")
        if not cert_url:
            raise AcmeError("Le serveur ACME n'a pas renvoyé d'URL de certificat.")

        status, _, chain = self._post(cert_url, None)
        if status >= 400 or not chain:
            raise AcmeError(f"Téléchargement du certificat échoué ({status}).")
        return chain, certs.key_to_pem(order_key)
