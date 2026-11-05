import httpx
from src.clients.wallet_client import WalletClient

def test_http_client():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={"status":"ok"})
    client=WalletClient('http://test',transport=httpx.MockTransport(handler))
    assert client.health()=={'status':'ok'}
    client.credentials('did:example:demo')
    assert seen[-1].url.params['status']=='all'
    client.close()

def test_http_error_propagates():
    import pytest
    client=WalletClient('http://test',transport=httpx.MockTransport(lambda r:httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError): client.record('missing')
    client.close()
