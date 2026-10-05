import os
import httpx

class DXWorksService:
    def __init__(self):
        self.host = os.getenv("DXWORKS_API_HOST", "https://api.dxworks.kr")
        self.api_key = os.getenv("DXWORKS_API_KEY", "")

    async def issue_mock_badge(self, template_id: str, holder_did: str) -> dict:
        url = f"{self.host}/api/v1/issuer/ob3/credentials"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "templateId": template_id,
            "holderDid": holder_did
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def verify_identity(self, vp_token: str) -> dict:
        url = f"{self.host}/api/v1/verifier/holder-presentations/verify"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "vpToken": vp_token
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def verify_badge(self, vc_jwt: str) -> dict:
        url = f"{self.host}/api/v1/verifier/ob3/verify"
        headers = {
            "Content-Type": "application/json",
        }
        payload = {
            "vcJwt": vc_jwt
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

dxworks_service = DXWorksService()
