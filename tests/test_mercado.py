import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator import mercado


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


LOC = {"center": {"lat": -16.7, "lng": -49.2}, "viewport": {"north": 1, "south": 0, "east": 1, "west": 0}}


def _falso_get(respostas, chamadas):
    def get(url, **kw):
        slug = url.rsplit("/", 1)[-1]
        chamadas.append(slug)
        r = respostas[slug]
        if isinstance(r, Exception):
            raise r
        return r
    return get


def test_cidade_sem_cobertura_e_consultada_uma_vez_so(monkeypatch):
    mercado._SLUG_CIDADE.clear()
    chamadas = []
    monkeypatch.setattr(mercado.requests, "get", _falso_get({"cidadezinha-go-brasil": _Resp(404)}, chamadas))
    assert mercado.resolver_localizacao("Cidadezinha", "GO", "Centro") is None
    assert mercado.resolver_localizacao("Cidadezinha", "GO", "Vila Nova") is None
    assert mercado.resolver_localizacao("Cidadezinha", "GO", "") is None
    assert chamadas == ["cidadezinha-go-brasil"]  # os bairros nem foram consultados


def test_erro_de_rede_nao_vira_sem_cobertura_pra_cidade_inteira(monkeypatch):
    mercado._SLUG_CIDADE.clear()
    chamadas = []
    monkeypatch.setattr(mercado.requests, "get",
                        _falso_get({"goiania-go-brasil": requests.ConnectionError("fora do ar")}, chamadas))
    assert mercado.resolver_localizacao("Goiania", "GO", "Setor Bueno") is None
    assert mercado.resolver_localizacao("Goiania", "GO", "Setor Bueno") is None
    assert chamadas.count("goiania-go-brasil") == 2  # tentou de novo, não memorizou a falha


def test_cidade_coberta_usa_bairro_e_cai_na_cidade_se_bairro_nao_existe(monkeypatch):
    mercado._SLUG_CIDADE.clear()
    chamadas = []
    resp = {
        "goiania-go-brasil": _Resp(200, LOC),
        "setor-bueno-goiania-go-brasil": _Resp(200, LOC),
        "bairro-inexistente-goiania-go-brasil": _Resp(404),
    }
    monkeypatch.setattr(mercado.requests, "get", _falso_get(resp, chamadas))
    com_bairro = mercado.resolver_localizacao("Goiania", "GO", "Setor Bueno")
    assert com_bairro["slug"] == "setor-bueno-goiania-go-brasil" and com_bairro["bairro_encontrado"] is True
    sem_bairro = mercado.resolver_localizacao("Goiania", "GO", "Bairro Inexistente")
    assert sem_bairro["slug"] == "goiania-go-brasil" and sem_bairro["bairro_encontrado"] is False
    assert chamadas.count("goiania-go-brasil") == 1  # cidade consultada uma vez só
