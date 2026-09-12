"""
Calculadora de retorno para investimento em leilão de imóveis.

Modelo baseado no fluxo padrão de aquisição (lance) -> reforma/posse -> venda,
com premissas de mercado brasileiro (comissão de leiloeiro, ITBI, corretagem,
imposto sobre ganho de capital). Todas as premissas percentuais têm um default
razoável mas podem ser sobrescritas por imóvel.
"""

from dataclasses import dataclass, field
from typing import Optional


# Premissas default (podem ser sobrescritas por análise)
DEFAULT_COMISSAO_LEILOEIRO_PCT = 0.05      # comissão do leiloeiro sobre o lance
DEFAULT_ITBI_PCT = 0.03                     # ITBI médio (varia por município, 2-4%)
DEFAULT_CUSTAS_CARTORIO_PCT = 0.015         # registro + escritura, aproximado
DEFAULT_CORRETAGEM_VENDA_PCT = 0.06         # comissão do corretor na venda
DEFAULT_IMPOSTO_GANHO_CAPITAL_PCT = 0.15    # IR sobre ganho de capital (pessoa física)


@dataclass
class InvestmentInputs:
    # --- Aquisição ---
    valor_lance: float                      # valor que será efetivamente pago no leilão
    valor_avaliacao: float                  # valor de avaliação/mercado do imóvel (referência)

    # --- Custos de aquisição (percentuais sobre valor_lance, salvo indicado) ---
    comissao_leiloeiro_pct: float = DEFAULT_COMISSAO_LEILOEIRO_PCT
    itbi_pct: float = DEFAULT_ITBI_PCT
    custas_cartorio_pct: float = DEFAULT_CUSTAS_CARTORIO_PCT
    advogado_due_diligence: float = 0.0      # valor fixo (due diligence, análise de matrícula)

    # --- Ocupação / desocupação ---
    ocupado: bool = False
    custo_desocupacao: float = 0.0           # ação de imissão na posse / despejo, se ocupado

    # --- Dívidas que podem recair sobre o imóvel ---
    divida_condominio_iptu_existente: float = 0.0

    # --- Reforma ---
    reforma: float = 0.0

    # --- Período de posse até a venda ---
    meses_posse: float = 6.0
    condominio_mensal: float = 0.0
    iptu_mensal: float = 0.0
    outros_custos_mensais: float = 0.0

    # --- Venda ---
    valor_venda_estimado: Optional[float] = None   # se None, usa valor_avaliacao
    corretagem_venda_pct: float = DEFAULT_CORRETAGEM_VENDA_PCT
    imposto_ganho_capital_pct: float = DEFAULT_IMPOSTO_GANHO_CAPITAL_PCT

    def venda_estimada(self) -> float:
        return self.valor_venda_estimado if self.valor_venda_estimado is not None else self.valor_avaliacao


@dataclass
class InvestmentResult:
    desconto_pct: float                # desconto do lance vs avaliação
    custo_total_aquisicao: float
    custo_total_posse: float
    investimento_total: float
    valor_liquido_venda: float
    lucro_bruto: float
    imposto_ganho_capital: float
    lucro_liquido: float
    roi_pct: float                     # retorno líquido sobre capital investido, no período
    roi_anualizado_pct: float          # roi_pct anualizado (juros compostos) pelo meses_posse

    def is_oportunidade(self, roi_anualizado_min_pct: float = 20.0) -> bool:
        return self.roi_anualizado_pct >= roi_anualizado_min_pct


def calcular(inputs: InvestmentInputs) -> InvestmentResult:
    if inputs.valor_lance <= 0:
        raise ValueError("valor_lance deve ser positivo")
    if inputs.valor_avaliacao <= 0:
        raise ValueError("valor_avaliacao deve ser positivo")
    if inputs.meses_posse <= 0:
        raise ValueError("meses_posse deve ser positivo")

    desconto_pct = (inputs.valor_avaliacao - inputs.valor_lance) / inputs.valor_avaliacao * 100

    comissao = inputs.valor_lance * inputs.comissao_leiloeiro_pct
    itbi = inputs.valor_lance * inputs.itbi_pct
    cartorio = inputs.valor_lance * inputs.custas_cartorio_pct
    desocupacao = inputs.custo_desocupacao if inputs.ocupado else 0.0

    custo_total_aquisicao = (
        inputs.valor_lance
        + comissao
        + itbi
        + cartorio
        + inputs.advogado_due_diligence
        + desocupacao
    )

    custo_total_posse = (
        inputs.condominio_mensal + inputs.iptu_mensal + inputs.outros_custos_mensais
    ) * inputs.meses_posse

    investimento_total = (
        custo_total_aquisicao
        + inputs.reforma
        + custo_total_posse
        + inputs.divida_condominio_iptu_existente
    )

    valor_venda = inputs.venda_estimada()
    valor_liquido_venda = valor_venda * (1 - inputs.corretagem_venda_pct)

    lucro_bruto = valor_liquido_venda - investimento_total
    imposto_ganho_capital = max(0.0, lucro_bruto) * inputs.imposto_ganho_capital_pct
    lucro_liquido = lucro_bruto - imposto_ganho_capital

    roi_pct = (lucro_liquido / investimento_total) * 100 if investimento_total > 0 else 0.0
    # base negativa (perda >= 100% do capital) não é elevável a potência fracionária
    if roi_pct <= -100:
        roi_anualizado_pct = -100.0
    else:
        roi_anualizado_pct = ((1 + roi_pct / 100) ** (12 / inputs.meses_posse) - 1) * 100

    return InvestmentResult(
        desconto_pct=desconto_pct,
        custo_total_aquisicao=custo_total_aquisicao,
        custo_total_posse=custo_total_posse,
        investimento_total=investimento_total,
        valor_liquido_venda=valor_liquido_venda,
        lucro_bruto=lucro_bruto,
        imposto_ganho_capital=imposto_ganho_capital,
        lucro_liquido=lucro_liquido,
        roi_pct=roi_pct,
        roi_anualizado_pct=roi_anualizado_pct,
    )
