"""
Análise rápida (triagem) para imóveis recém-raspados dos sites de leilão.

Usa premissas conservadoras/genéricas quando o imóvel não tem custos
detalhados ainda (reforma, condomínio real, etc.) — serve para RANKEAR
oportunidades e decidir em quais vale a pena investigar a fundo (matrícula,
visita, cotação de reforma). Não substitui a análise fina antes de dar lance.

O valor de venda estimado tenta usar comparáveis reais de mercado (QuintoAndar,
via calculator/mercado.py) quando o imóvel tem área conhecida e a região é
coberta; quando não dá, cai num haircut genérico em cima da avaliação do banco.
"""

from typing import Optional

from db.store import Listing

from .model import InvestmentInputs, InvestmentResult, calcular

REFORMA_PCT_LANCE = 0.10         # estimativa genérica de reforma, como % do lance (valor pago no leilão)
HAIRCUT_VENDA_PCT = 0.05         # desconta a avaliação do banco (tende a ser otimista) — usado só quando não há comparáveis
MESES_POSSE_DEFAULT = 8.0
CUSTO_DESOCUPACAO_PCT_LANCE = 0.05  # estimativa se o imóvel estiver ocupado

# Bairros grandes/heterogêneos (ex: Butantã em SP cobre da Vila Butantã cara até
# áreas bem mais simples longe dali) fazem o comparável do QuintoAndar "vazar"
# pra uma sub-região bem mais cara do que a do imóvel real, gerando venda
# estimada 3-5x a avaliação do banco e ROI de centenas de % — óbvio demais pra
# ser real. Nesses casos é mais seguro desconfiar do comparável e cair no
# haircut do que confiar cegamente na mediana de mercado.
DESVIO_MAXIMO_VS_AVALIACAO = 1.5


def _estimar_venda(listing: Listing, preco_m2_mercado: Optional[float]) -> tuple[float, str]:
    area = getattr(listing, "area_m2", None)
    if preco_m2_mercado and area:
        valor_comparaveis = preco_m2_mercado * area
        if valor_comparaveis <= listing.valor_avaliacao * DESVIO_MAXIMO_VS_AVALIACAO:
            return valor_comparaveis, "comparaveis_quintoandar"
        # comparável implausível (provavelmente bairro heterogêneo/geo mal resolvida) — não confia
    return listing.valor_avaliacao * (1 - HAIRCUT_VENDA_PCT), "haircut_avaliacao"


def montar_inputs_rapidos(listing: Listing, preco_m2_mercado: Optional[float] = None,
                           arremate_info: Optional[dict] = None) -> Optional[InvestmentInputs]:
    """Monta InvestmentInputs com premissas padrão. Retorna None se faltar dado essencial.

    `preco_m2_mercado`: preço/m² mediano de comparáveis à venda na região do imóvel
    (ver calculator/mercado.py) — quando informado e o imóvel tem área conhecida,
    substitui o haircut genérico como base do valor de venda estimado.

    `arremate_info`: resumo de arremates reais recentes na região (ver
    calculator/arremates.py) — não entra na conta do valor de venda (é só
    complemento informativo, exibido no dashboard), já que é uma amostra
    pequena demais pra usar como premissa de cálculo com segurança.
    """
    if not listing.valor_lance_atual or not listing.valor_avaliacao:
        return None

    ocupado = listing.ocupado == "sim"
    valor_venda, fonte_venda = _estimar_venda(listing, preco_m2_mercado)
    inputs = InvestmentInputs(
        valor_lance=listing.valor_lance_atual,
        valor_avaliacao=listing.valor_avaliacao,
        reforma=listing.valor_lance_atual * REFORMA_PCT_LANCE,
        ocupado=ocupado,
        custo_desocupacao=listing.valor_lance_atual * CUSTO_DESOCUPACAO_PCT_LANCE if ocupado else 0.0,
        meses_posse=MESES_POSSE_DEFAULT,
        valor_venda_estimado=valor_venda,
        fonte_venda_estimada=fonte_venda,
    )
    if arremate_info:
        inputs.arremate_recente_valor = arremate_info.get("ultimo_valor")
        inputs.arremate_recente_data = arremate_info.get("ultimo_data") or ""
        inputs.arremate_recente_endereco = arremate_info.get("ultimo_endereco") or ""
        inputs.arremate_recente_preco_m2 = arremate_info.get("mediana_preco_m2")
        inputs.arremate_recente_n_amostra = arremate_info.get("n_amostra") or 0
    return inputs


def analise_rapida(listing: Listing, preco_m2_mercado: Optional[float] = None,
                    arremate_info: Optional[dict] = None) -> Optional[tuple[InvestmentInputs, InvestmentResult]]:
    inputs = montar_inputs_rapidos(listing, preco_m2_mercado, arremate_info)
    if inputs is None:
        return None
    return inputs, calcular(inputs)
