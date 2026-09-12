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
