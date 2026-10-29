import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import zukerman


class _Resp:
    def __init__(self, status, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def test_get_espera_e_tenta_de_novo_no_429(monkeypatch):
    respostas = [_Resp(429, {"Retry-After": "3"}), _Resp(429), _Resp(200, text="ok")]
    esperas = []
    monkeypatch.setattr(zukerman.requests, "get", lambda *a, **k: respostas.pop(0))
    monkeypatch.setattr(zukerman.time, "sleep", esperas.append)
    assert zukerman._get("http://x/pagina").text == "ok"
    assert esperas == [3, 16]  # Retry-After respeitado; sem ele, backoff 8s * 2^tentativa


def test_get_desiste_depois_de_varias_tentativas(monkeypatch):
    monkeypatch.setattr(zukerman.requests, "get", lambda *a, **k: _Resp(429))
    monkeypatch.setattr(zukerman.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        zukerman._get("http://x/pagina")


def test_get_nao_tenta_de_novo_em_404(monkeypatch):
    chamadas = []
    monkeypatch.setattr(zukerman.requests, "get", lambda *a, **k: chamadas.append(1) or _Resp(404))
    monkeypatch.setattr(zukerman.time, "sleep", lambda s: None)
    with pytest.raises(requests.HTTPError):
        zukerman._get("http://x/pagina")
    assert len(chamadas) == 1
