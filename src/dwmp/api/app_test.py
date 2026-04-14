from httpx import ASGITransport, AsyncClient

from dwmp.api import auth as auth_module
from dwmp.api.app import create_app


async def test_health_check():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


async def test_list_carriers():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/carriers")

    assert response.status_code == 200
    carriers = response.json()
    names = {c["name"] for c in carriers}
    assert names == {"amazon", "dhl", "dpd", "gls", "postnl"}
    assert all("auth_type" in c for c in carriers)


async def test_ingress_header_prefixes_redirect(monkeypatch):
    """X-Ingress-Path header from a reverse proxy must propagate into redirect Locations
    so HA ingress (and any other path-prefixing proxy) can route the response correctly.
    """
    monkeypatch.setattr(auth_module, "PASSWORD_HASH", auth_module.set_password("x"))
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False,
    ) as client:
        response = await client.get(
            "/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/hassio_ingress/abc/login"


async def test_no_ingress_header_keeps_unprefixed_redirect(monkeypatch):
    """Regression guard for k8s/direct-port deployments — without the header,
    redirects must remain absolute-from-root just like before."""
    monkeypatch.setattr(auth_module, "PASSWORD_HASH", auth_module.set_password("x"))
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False,
    ) as client:
        response = await client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_ingress_header_strips_trailing_slash(monkeypatch):
    """Ingress prefix with a trailing slash must not produce double-slash Locations."""
    monkeypatch.setattr(auth_module, "PASSWORD_HASH", auth_module.set_password("x"))
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False,
    ) as client:
        response = await client.get(
            "/", headers={"X-Ingress-Path": "/proxy/abc/"},
        )

    assert response.headers["location"] == "/proxy/abc/login"


async def test_extension_updates_xml_is_public(monkeypatch):
    """The extension update endpoint must be accessible without authentication."""
    monkeypatch.setattr(auth_module, "PASSWORD_HASH", auth_module.set_password("x"))
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/extension/updates.xml")

    assert response.status_code == 200
    assert "gupdate" in response.text


async def test_static_files_serve_under_ingress_header():
    """Regression: setting scope['root_path'] breaks Starlette's StaticFiles mount
    because the mount slices the request path by len(root_path). The middleware
    must keep static-asset routing intact when the X-Ingress-Path header is present.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_no_header = await client.get("/static/icon-64.png")
        response_with_header = await client.get(
            "/static/icon-64.png", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"},
        )

    assert response_no_header.status_code == 200
    assert response_with_header.status_code == 200
    assert response_no_header.content == response_with_header.content
