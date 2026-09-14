"""
Busca de comparáveis de mercado (imóveis à venda) no QuintoAndar, pra estimar
um valor de venda mais realista do que aplicar um haircut genérico em cima da
avaliação do banco/leiloeiro.

QuintoAndar tem uma API pública (sem autenticação, sem proteção anti-bot) que
dá pra usar em duas etapas:
1. resolver o bairro/cidade num slug -> centro + viewport geográfico
2. buscar os imóveis à venda dentro desse viewport, com preço e área

Cobre bem apartamento/casa em cidades grandes (SP, RJ, BH, Campinas, etc.).
Não cobre terreno (QuintoAndar não lista terrenos) nem cidades pequenas fora
da base deles — nesses casos as funções retornam None e quem chama cai de
volta pro haircut sobre a avaliação.
"""

import re
import unicodedata
from typing import Optional

import requests

BASE_URL = "https://apigw.prod.quintoandar.com.br/house-listing-search"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; leiloes-imoveis-bot/1.0)", "Content-Type": "application/json"}
TIMEOUT = 15

# Casa e apartamento têm R$/m² bem diferentes (terreno, condomínio, padrão
# construtivo) — misturar os dois na mesma mediana é o que causava estimativa
# viesada (uma casa de condomínio de R$2M puxando a mediana de apartamentos
# simples lá pra cima). Por isso os comparáveis só entram na conta se forem
# do mesmo grupo do imóvel do leilão.
GRUPOS_TIPO = {
    "apartamento": {"apartamento", "studiooukitchenette", "flat", "kitnet", "loft", "cobertura"},
    "casa": {"casa", "casacondominio", "sobrado"},
}


def grupo_tipo(tipo_imovel: str) -> str:
    t = (tipo_imovel or "").strip().lower()
    if not t:
        return ""
    for grupo, chaves in GRUPOS_TIPO.items():
        if any(chave in t for chave in chaves):
            return grupo
    return ""


def slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9\s-]", "", texto).strip().lower()
    return re.sub(r"[\s_]+", "-", texto)


def _montar_slug(cidade: str, estado: str, bairro: str = "") -> str:
    partes = [p for p in [bairro, cidade, estado] if p]
    return "-".join(slugify(p) for p in partes) + "-brasil"


def resolver_localizacao(cidade: str, estado: str, bairro: str = "") -> Optional[dict]:
    """Resolve (bairro, cidade, UF) num centro geográfico + viewport via QuintoAndar.

    Tenta primeiro com o bairro; se não achar (cidade pequena ou bairro fora da
    base deles), tenta só cidade+UF como fallback mais genérico.
    """
    tentativas = [_montar_slug(cidade, estado, bairro)] if bairro else []
    tentativas.append(_montar_slug(cidade, estado))
    for slug in tentativas:
        try:
            resp = requests.get(f"{BASE_URL}/v1/search/location/slug/{slug}", params={"country": "BR"},
                                 headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            data = resp.json()
            if data.get("center") and data.get("viewport"):
                return {"slug": slug, "center": data["center"], "viewport": data["viewport"],
                        "bairro_encontrado": bool(bairro) and slug == tentativas[0]}
    return None


def buscar_comparaveis(cidade: str, estado: str, bairro: str = "", grupo: str = "", limite: int = 80) -> list[dict]:
    """Busca imóveis à venda na região resolvida. Retorna [] se a região não for reconhecida.

    `grupo` ("casa" ou "apartamento", ver `grupo_tipo`) filtra os comparáveis pro
    mesmo tipo do imóvel do leilão — comparar apartamento com apartamento, não
    com casa de condomínio do lado, que tem outra faixa de R$/m² inteiramente."""
    local = resolver_localizacao(cidade, estado, bairro)
    if local is None:
        return []

    body = {
        "slug": local["slug"],
        "topics": [],
        "fields": ["id", "salePrice", "area", "address", "regionName", "city", "type", "bedrooms"],
        "sorting": {"criteria": "RELEVANCE", "order": "DESC"},
        "pagination": {"pageSize": limite, "offset": 0},
        "context": {"listShowing": True, "mapShowing": True, "numPhotos": 1, "isSSR": False},
        "filters": {
            "unknownSlugs": [], "enableFlexibleSearch": True, "businessContext": "SALE",
            "location": {"coordinate": local["center"], "viewport": local["viewport"],
                         "neighborhoods": [], "countryCode": "BR"},
            "priceRange": [], "availability": "ANY", "occupancy": "ANY", "partnerIds": [],
            "specialConditions": [], "excludedSpecialConditions": [], "blocklist": [], "selectedHouses": [],
            "categories": [],
            "houseSpecs": {"area": {"range": {}}, "houseTypes": [], "amenities": [], "installations": [],
                           "bathrooms": {"range": {}}, "bedrooms": {"range": {}},
                           "parkingSpace": {"range": {}}, "suites": {"range": {}}},
            "origin": "HYBRID",
        },
        "locationDescriptions": [{"description": local["slug"]}],
    }
    try:
        resp = requests.post(f"{BASE_URL}/v3/search/list", json=body, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []

    comparaveis = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        if not (src.get("salePrice") and src.get("area")):
            continue
        if grupo and grupo_tipo(src.get("type", "")) != grupo:
            continue
        comparaveis.append({
            "preco": src["salePrice"],
            "area_m2": src["area"],
            "preco_m2": src["salePrice"] / src["area"],
            "tipo": src.get("type", ""),
            "bairro": src.get("regionName", ""),
            "endereco": src.get("address", ""),
        })
    return comparaveis


def preco_m2_mediano(comparaveis: list[dict]) -> Optional[float]:
    valores = sorted(c["preco_m2"] for c in comparaveis)
    if not valores:
        return None
    n = len(valores)
    meio = n // 2
    return valores[meio] if n % 2 else (valores[meio - 1] + valores[meio]) / 2


def estimar_preco_m2_regiao(cidade: str, estado: str, bairro: str, tipo_imovel: str,
                             minimo_amostra: int = 5) -> Optional[dict]:
    """Preço/m² mediano de comparáveis do MESMO GRUPO (casa/apartamento) do imóvel
    do leilão, na região — independente do imóvel específico, então dá pra
    cachear por (cidade, estado, bairro, grupo) e reusar pra qualquer imóvel
    raspado naquele bairro com tipo parecido.

    Retorna None se: o tipo do imóvel não é casa nem apartamento (ex: terreno —
    o QuintoAndar não lista terreno, comparar não faz sentido), a região não é
    coberta pelo QuintoAndar, ou há poucos comparáveis do mesmo grupo pra
    confiar na mediana (evita misturar apartamento simples com casa de
    condomínio de R$2M do lado, que já causou estimativa 2x inflada aqui)."""
    grupo = grupo_tipo(tipo_imovel)
    if not grupo:
        return None
    comparaveis = buscar_comparaveis(cidade, estado, bairro, grupo=grupo)
    if len(comparaveis) < minimo_amostra:
        return None
    preco_m2 = preco_m2_mediano(comparaveis)
    if preco_m2 is None:
        return None
    return {"preco_m2_mediano": preco_m2, "n_comparaveis": len(comparaveis), "grupo": grupo, "fonte": "quintoandar"}


def estimar_valor_venda(cidade: str, estado: str, bairro: str, tipo_imovel: str,
                         area_m2: Optional[float], minimo_amostra: int = 5) -> Optional[dict]:
    """Conveniência pra uso direto/teste: resolve preço/m² da região e já aplica a área."""
    base = estimar_preco_m2_regiao(cidade, estado, bairro, tipo_imovel, minimo_amostra)
    if base is None or not area_m2:
        return base
    resultado = dict(base)
    resultado["valor_estimado"] = base["preco_m2_mediano"] * area_m2
    return resultado


if __name__ == "__main__":
    r = estimar_valor_venda("São Paulo", "SP", "Vila Madalena", "Apartamento", area_m2=70)
    print(r)
    r2 = estimar_valor_venda("Vespasiano", "MG", "Nova Pampulha", "Apartamento", area_m2=45)
    print(r2)
    r3 = estimar_valor_venda("Araras", "SP", "Jardim Cândida", "Casa", area_m2=None)
    print(r3)
