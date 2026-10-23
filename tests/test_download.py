import asyncio
from unittest.mock import AsyncMock
import pytest
from niba_test import api


class Response:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = headers or {}
        self.chunks = chunks or [b'%PDF-1.7\nfixture']
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.parametrize('target', ['http://example.com/file','https://127.0.0.1/file',
                                    'https://user:password@example.com/file','https://example.com:8123/file'])
def test_reject_unsafe_download(monkeypatch, target):
    async def run():
        client = api.NibaApiClient(None,'','')
        client.get_document = AsyncMock(return_value={'document':target})
        with pytest.raises(api.NibaApiError):
            await client.get_invoice_pdf('document')
    asyncio.run(run())


def test_no_token_forwarded_and_redirect_validation(monkeypatch):
    calls = []
    responses = [Response(302, {'Location':'https://cdn.example.com/file'}), Response()]
    class Session:
        def __init__(self, **kwargs):
            assert 'headers' not in kwargs and 'auth' not in kwargs
            self.connector = kwargs['connector']
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.connector.close()
        def get(self, link, **kwargs):
            calls.append((link,kwargs))
            assert kwargs == {'headers':{'Accept':'application/pdf'},'allow_redirects':False}
            return responses.pop(0)
    monkeypatch.setattr(api.aiohttp,'ClientSession',Session)
    async def run():
        client=api.NibaApiClient(None,'private','private');client._token='secret'
        client.get_document=AsyncMock(return_value={'document':'https://example.com/file'})
        assert (await client.get_invoice_pdf('doc')).startswith(b'%PDF-')
    asyncio.run(run())
    assert len(calls)==2


def test_dns_private_destination_rejected(monkeypatch):
    async def resolve(*args): return [{'host':'192.168.1.1'}]
    monkeypatch.setattr(api.aiohttp.resolver.ThreadedResolver,'resolve',resolve)
    async def run():
        resolver = api._PublicResolver()
        with pytest.raises(OSError): await resolver.resolve('example.com',443)
        await resolver.close()
    asyncio.run(run())
