from httpx import ASGITransport, AsyncClient

from dwmp.api.app import create_app


async def test_health_check():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_list_carriers():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/carriers")

    assert response.status_code == 200
    carriers = response.json()
    names = {c["name"] for c in carriers}
    assert names == {"dhl", "dpd", "postnl"}
    assert all("auth_type" in c for c in carriers)
