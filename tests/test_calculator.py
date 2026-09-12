import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator import InvestmentInputs, calcular


def test_caso_realista_lucrativo():
    inp = InvestmentInputs(
        valor_lance=300_000,
        valor_avaliacao=500_000,
        reforma=40_000,
        meses_posse=8,
        condominio_mensal=600,
        iptu_mensal=150,
        valor_venda_estimado=460_000,
    )
    r = calcular(inp)
    assert r.desconto_pct == 40.0
    assert r.investimento_total > inp.valor_lance
    assert r.roi_pct > 0
    assert r.roi_anualizado_pct > r.roi_pct  # anualização amplifica retorno positivo em < 12 meses


def test_imovel_ocupado_com_desocupacao_cara_pode_zerar_retorno():
    inp = InvestmentInputs(
        valor_lance=300_000,
        valor_avaliacao=320_000,  # pouco desconto
        ocupado=True,
        custo_desocupacao=60_000,
        reforma=30_000,
        meses_posse=10,
        condominio_mensal=500,
        valor_venda_estimado=340_000,
    )
    r = calcular(inp)
    assert r.lucro_liquido < 0


def test_perda_total_nao_quebra_anualizacao():
    inp = InvestmentInputs(
        valor_lance=300_000,
        valor_avaliacao=300_000,
        meses_posse=6,
        valor_venda_estimado=0,  # cenário extremo: venda não cobre nem o investimento
    )
    r = calcular(inp)
    assert r.roi_pct <= -100
    assert r.roi_anualizado_pct == -100.0


if __name__ == "__main__":
    test_caso_realista_lucrativo()
    test_imovel_ocupado_com_desocupacao_cara_pode_zerar_retorno()
    test_perda_total_nao_quebra_anualizacao()
    print("OK - todos os testes passaram")
