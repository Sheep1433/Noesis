from server.main import app


def test_user_provider_routes_are_not_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/user/providers" not in paths
    assert not any(path.startswith("/api/user/providers/") for path in paths)
    assert not any(path.startswith("/api/user/model-bindings") for path in paths)
    assert "/api/models" in paths
