"""
Análise rápida (triagem) para imóveis recém-raspados dos sites de leilão.

Usa premissas conservadoras/genéricas quando o imóvel não tem custos
detalhados ainda (reforma, condomínio real, etc.) — serve para RANKEAR
oportunidades e decidir em quais vale a pena investigar a fundo (matrícula,
visita, cotação de reforma). Não substitui a análise fina antes de dar lance.
"""

from typing import Optional

from db.store import Listing

from .model import InvestmentInputs, InvestmentResult, calcular

REFORMA_PCT_AVALIACAO = 0.08     # estimativa genérica de reforma leve/média
HAIRCUT_VENDA_PCT = 0.05         # desconta a avaliação do banco (tende a ser otimista)
MESES_POSSE_DEFAULT = 8.0
CUSTO_DESOCUPACAO_PCT_LANCE = 0.05  # estimativa se o imóvel estiver ocupado


def montar_inputs_rapidos(listing: Listing) -> Optional[InvestmentInputs]:
    """Monta InvestmentInputs com premissas padrão. Retorna None se faltar dado essencial."""
    if not listing.valor_lance_atual or not listing.valor_avaliacao:
        return None

    ocupado = listing.ocupado == "sim"
    return InvestmentInputs(
        valor_lance=listing.valor_lance_atual,
        valor_avaliacao=listing.valor_avaliacao,
        reforma=listing.valor_avaliacao * REFORMA_PCT_AVALIACAO,
        ocupado=ocupado,
        custo_desocupacao=listing.valor_lance_atual * CUSTO_DESOCUPACAO_PCT_LANCE if ocupado else 0.0,
        meses_posse=MESES_POSSE_DEFAULT,
        valor_venda_estimado=listing.valor_avaliacao * (1 - HAIRCUT_VENDA_PCT),
    )


def analise_rapida(listing: Listing) -> Optional[tuple[InvestmentInputs, InvestmentResult]]:
    inputs = montar_inputs_rapidos(listing)
    if inputs is None:
        return None
    return inputs, calcular(inputs)
