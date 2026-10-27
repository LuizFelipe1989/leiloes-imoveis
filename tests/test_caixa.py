import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from scraper import caixa


class _PaginaFalsa:
    """Imita page.evaluate devolvendo uma sequência pré-definida de respostas."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def evaluate(self, js, url):
        self.chamadas += 1
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _csv_b64(n_linhas: int) -> str:
    linhas = ["Lista de imoveis - gerado em 01/01/2026", "N° do imóvel;Descrição;UF"]
    linhas += [f"{i};Casa, 50.00 de área total;SP" for i in range(n_linhas)]
    return base64.b64encode("\n".join(linhas).encode("cp1252")).decode()


def test_baixar_csv_ok(monkeypatch):
    monkeypatch.setattr(caixa.time, "sleep", lambda s: None)
    page = _PaginaFalsa([{"status": 200, "base64": _csv_b64(3)}])
    assert len(caixa._baixar_csv(page, "SP", "http://x")) == 3


def test_baixar_csv_tenta_de_novo_e_recupera(monkeypatch):
    monkeypatch.setattr(caixa.time, "sleep", lambda s: None)
    page = _PaginaFalsa([
        {"status": 503, "base64": None},
        Exception("AbortError: signal timed out"),
        {"status": 200, "base64": _csv_b64(2)},
    ])
    assert len(caixa._baixar_csv(page, "MG", "http://x")) == 2
    assert page.chamadas == 3


def test_baixar_csv_falha_levanta_em_vez_de_devolver_parcial(monkeypatch):
    # antes o scraper pulava a UF e devolvia o resto como "sucesso", o que fazia
    # os imóveis daquela UF serem fechados como "sumidos da fonte"
    monkeypatch.setattr(caixa.time, "sleep", lambda s: None)
    page = _PaginaFalsa([{"status": 403, "base64": None}] * 3)
    with pytest.raises(RuntimeError, match="MG"):
        caixa._baixar_csv(page, "MG", "http://x")


def test_baixar_csv_vazio_conta_como_falha(monkeypatch):
    monkeypatch.setattr(caixa.time, "sleep", lambda s: None)
    page = _PaginaFalsa([{"status": 200, "base64": _csv_b64(0)}] * 3)
    with pytest.raises(RuntimeError):
        caixa._baixar_csv(page, "SP", "http://x")


def test_ultima_linha_pega_a_excecao_real_do_traceback():
    tb = (
        "Traceback (most recent call last):\n"
        "  File \"x.py\", line 1, in <module>\n"
        "playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded.\n"
    )
    assert cli._ultima_linha(tb).startswith("playwright._impl._errors.TimeoutError")
    assert cli._ultima_linha("") == "sem saída de erro"


@pytest.mark.parametrize("rotulo, modalidade, praca", [
    ("Leilão SFI - Edital Único", "leilao", ""),
    ("Licitação Aberta", "leilao", "Licitação Aberta"),
    ("Venda Online", "venda_online", "Venda Online"),
    ("Venda Direta Online", "venda_direta", "Venda Direta"),
    ("  venda direta online ", "venda_direta", "Venda Direta"),   # espaços/caixa não importam
])
def test_modalidade_do_csv_oficial(rotulo, modalidade, praca):
    assert caixa._modalidade_do_csv(rotulo) == (modalidade, praca)


def test_modalidade_desconhecida_do_csv_devolve_vazio_em_vez_de_chutar(capsys):
    assert caixa._modalidade_do_csv("Venda Presencial Nova") == ("", "")
    assert "desconhecida" in capsys.readouterr().err


def test_row_to_listing_usa_a_coluna_modalidade_de_venda():
    row = {"N° do imóvel": "123", "Descrição": "Casa, 50.00 de área total", "UF": "SP",
           "Modalidade de venda": "Venda Direta Online"}
    l = caixa._row_to_listing(row)
    assert (l.modalidade, l.praca, l.banco) == ("venda_direta", "Venda Direta", "Caixa")
