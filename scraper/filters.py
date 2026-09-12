"""Filtro de foco: por ora só interessam imóveis residenciais e terrenos."""

COMERCIAL_KEYWORDS = [
    "comercial", "comerciais", "loja", "lojas", "galpão", "galpao",
    "sala", "salas", "prédio", "predio", "prédios", "predios",
    "escritório", "escritorio", "conjunto", "industrial", "industriais",
    "barracão", "barracao", "ponto comercial", "negócios", "negocios",
    "instalações", "instalacoes",
]


def eh_residencial_ou_terreno(tipo_imovel: str) -> bool:
    """True se o tipo do imóvel é residencial/terreno (ou desconhecido — não descarta por falta de dado)."""
    t = (tipo_imovel or "").strip().lower()
    if not t:
        return True
    return not any(k in t for k in COMERCIAL_KEYWORDS)


TERRENO_KEYWORDS = ["terreno", "lote", "gleba"]
RURAL_KEYWORDS = ["rural", "fazenda", "sítio", "sitio", "chácara", "chacara"]
OUTRO_KEYWORDS = ["vaga", "garagem"]
RESIDENCIAL_KEYWORDS = [
    "casa", "apartamento", "sobrado", "cobertura", "kitnet", "flat",
    "residenc", "condomínio", "condominio", "duplex", "studio", "estúdio",
]


def categoria_imovel(tipo_imovel: str) -> str:
    """Classifica o tipo bruto (que varia por fonte) numa categoria comum pro filtro do dashboard."""
    t = (tipo_imovel or "").strip().lower()
    if not t:
        return "Outro"
    if any(k in t for k in TERRENO_KEYWORDS):
        return "Terreno"
    if any(k in t for k in RURAL_KEYWORDS):
        return "Rural"
    if any(k in t for k in OUTRO_KEYWORDS):
        return "Outro"
    if any(k in t for k in RESIDENCIAL_KEYWORDS):
        return "Residencial"
    return "Outro"
