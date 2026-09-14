import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator.screening import montar_inputs_rapidos, DESVIO_MAXIMO_VS_AVALIACAO


def _listing(**kwargs):
    base = dict(valor_lance_atual=200_000, valor_avaliacao=250_000, ocupado="nao", area_m2=50)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_usa_comparaveis_quando_plausivel():
    # preco_m2 * area = 6000 * 50 = 300_000 -> 1.2x a avaliação, dentro do limite
    inputs = montar_inputs_rapidos(_listing(), preco_m2_mercado=6000)
    assert inputs.fonte_venda_estimada == "comparaveis_quintoandar"
    assert inputs.valor_venda_estimado == 300_000


def test_descarta_comparavel_implausivel_e_cai_no_haircut():
    # preco_m2 * area = 20000 * 50 = 1_000_000 -> 4x a avaliação, implausível
    # (bairro heterogêneo / geo mal resolvida) — deve cair pro haircut
    inputs = montar_inputs_rapidos(_listing(), preco_m2_mercado=20_000)
    assert inputs.fonte_venda_estimada == "haircut_avaliacao"
    assert inputs.valor_venda_estimado == 250_000 * 0.95


def test_sem_area_ou_sem_preco_m2_usa_haircut():
    inputs = montar_inputs_rapidos(_listing(area_m2=None), preco_m2_mercado=6000)
    assert inputs.fonte_venda_estimada == "haircut_avaliacao"
    inputs2 = montar_inputs_rapidos(_listing(), preco_m2_mercado=None)
    assert inputs2.fonte_venda_estimada == "haircut_avaliacao"


if __name__ == "__main__":
    test_usa_comparaveis_quando_plausivel()
    test_descarta_comparavel_implausivel_e_cai_no_haircut()
    test_sem_area_ou_sem_preco_m2_usa_haircut()
    print(f"OK - todos os testes passaram (limite atual: {DESVIO_MAXIMO_VS_AVALIACAO}x)")
