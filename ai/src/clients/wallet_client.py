from urllib.parse import quote
import httpx

class WalletClient:
    def __init__(self, base_url, transport=None):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30, transport=transport)
    def close(self):
        self.client.close()
    def _request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    def health(self):
        return self._request("GET", "/health")
    def credentials(self, did, status="all"):
        return self._request("GET", f"/v1/subjects/{quote(did, safe='')}/credentials", params={"status": status})
    def skills(self, did):
        return self._request("GET", f"/v1/subjects/{quote(did, safe='')}/skills")
    def record(self, record_id):
        return self._request("GET", f"/v1/records/{quote(record_id, safe='')}")
    def verification(self, record_id):
        return self._request("GET", f"/v1/records/{quote(record_id, safe='')}/verification")
    def verify(self, external_id):
        return self._request("POST", "/v1/verify", json={"external_id": external_id})
