import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator.screening import (
    montar_inputs_rapidos, montar_inputs_venda_direta, analise_rapida,
    DESVIO_MAXIMO_VS_AVALIACAO, HAIRCUT_VENDA_PCT,
)


def _listing(**kwargs):
    base = dict(valor_lance_atual=200_000, valor_avaliacao=250_000, ocupado="nao", area_m2=50,
                tipo_imovel="Apartamento")
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


def test_arremate_info_e_so_metadado_nao_afeta_valor_venda():
    arremate_info = {
        "ultimo_valor": 999_999, "ultimo_data": "2026-09-10 10:00:00",
        "ultimo_endereco": "Rua Teste, 123", "mediana_preco_m2": 7000, "n_amostra": 4,
    }
    inputs = montar_inputs_rapidos(_listing(), preco_m2_mercado=6000, arremate_info=arremate_info)
    # continua vindo dos comparáveis (6000 * 50 = 300_000), arremate não entra na conta
    assert inputs.fonte_venda_estimada == "comparaveis_quintoandar"
    assert inputs.valor_venda_estimado == 300_000
    assert inputs.arremate_recente_valor == 999_999
    assert inputs.arremate_recente_n_amostra == 4

    sem_arremate = montar_inputs_rapidos(_listing(), preco_m2_mercado=6000, arremate_info=None)
    assert sem_arremate.arremate_recente_valor is None
    assert sem_arremate.arremate_recente_n_amostra == 0


if __name__ == "__main__":
    test_usa_comparaveis_quando_plausivel()
    test_descarta_comparavel_implausivel_e_cai_no_haircut()
    test_sem_area_ou_sem_preco_m2_usa_haircut()
    test_arremate_info_e_so_metadado_nao_afeta_valor_venda()
    print(f"OK - todos os testes passaram (limite atual: {DESVIO_MAXIMO_VS_AVALIACAO}x)")


# ---- modalidades: leilão / venda online / venda direta ----

def test_comissao_de_leiloeiro_por_modalidade():
    comissao = {}
    for modalidade in ("leilao", "venda_online", "venda_direta"):
        inputs, _ = analise_rapida(_listing(), preco_m2_mercado=6000, modalidade=modalidade)
        comissao[modalidade] = inputs.comissao_leiloeiro_pct
    assert comissao == {"leilao": 0.05, "venda_online": 0.0, "venda_direta": 0.0}


def test_venda_online_usa_avaliacao_do_banco_como_referencia():
    # sem comparável cai no haircut sobre a avaliação, igual ao leilão (só muda a comissão)
    inputs, _ = analise_rapida(_listing(), preco_m2_mercado=None, modalidade="venda_online")
    assert inputs.fonte_venda_estimada == "haircut_avaliacao"
    assert inputs.valor_venda_estimado == 250_000 * (1 - HAIRCUT_VENDA_PCT)


def test_venda_direta_sem_comparavel_ou_area_nao_e_analisada():
    assert analise_rapida(_listing(), preco_m2_mercado=None, modalidade="venda_direta") is None
    assert analise_rapida(_listing(area_m2=None), preco_m2_mercado=6000, modalidade="venda_direta") is None


def test_venda_direta_usa_comparavel_plausivel():
    # 6000 * 50 = 300_000 = 1.2x a avaliação, dentro do limite
    inputs = montar_inputs_venda_direta(_listing(), preco_m2_mercado=6000)
    assert inputs.fonte_venda_estimada == "comparaveis_quintoandar"
    assert inputs.valor_avaliacao == 300_000 and inputs.valor_venda_estimado == 300_000


def test_venda_direta_desconfia_de_comparavel_implausivel():
    # 20000 * 50 = 1_000_000 = 4x a avaliação do banco: bairro heterogêneo, não confia
    inputs = montar_inputs_venda_direta(_listing(), preco_m2_mercado=20_000)
    assert inputs.fonte_venda_estimada == "haircut_avaliacao"
    assert inputs.valor_venda_estimado == 250_000 * (1 - HAIRCUT_VENDA_PCT)
    assert inputs.comissao_leiloeiro_pct == 0.0


def test_venda_direta_sem_avaliacao_do_banco_aceita_o_comparavel():
    # caso do BB: não existe avaliação pra servir de régua, o comparável vale como está
    inputs = montar_inputs_venda_direta(_listing(valor_avaliacao=None), preco_m2_mercado=20_000)
    assert inputs.fonte_venda_estimada == "comparaveis_quintoandar"
    assert inputs.valor_venda_estimado == 1_000_000
