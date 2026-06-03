from bolle.api_client import HttpAziendaApi, InMemoryAziendaApi, build_api
from bolle.config import ApiConfig


def test_build_api_memory():
    api = build_api(ApiConfig(backend="memory"))
    assert isinstance(api, InMemoryAziendaApi)
    # In offline non risolve nulla -> il codice finisce in revisione.
    assert api.cross_reference("ACME", "F-1") is None
    assert api.esiste_articolo("X") is False


def test_build_api_http():
    api = build_api(ApiConfig(backend="http", base_url="http://x:1"))
    assert isinstance(api, HttpAziendaApi)


def test_build_api_backend_sconosciuto():
    import pytest

    with pytest.raises(ValueError):
        build_api(ApiConfig(backend="boh"))
