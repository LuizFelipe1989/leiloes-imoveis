"""
Scraper da Sold Leilões (sold.com.br).

Sold usa a API pública da Superbid (offer-query.superbid.net) para listar
ofertas — não tem proteção anti-bot e devolve JSON estruturado, então esse é
o scraper mais simples e robusto dos três.
"""

import sys
from pathlib import Path
from typing import Iterable, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.store import Listing

API_URL = "https://offer-query.superbid.net/offers/"
# stores.id 1161/1741 = Sold / Sold Maisativo, os operadores usados em sold.com.br
FILTER = "product.productType.description:imoveis;stores.id:[1161,1741]"
PAGE_SIZE = 100


def _template_value(offer: dict, prop_id: str) -> str:
    for group in offer.get("product", {}).get("template", {}).get("groups", []):
        for prop in group.get("properties", []):
            if prop.get("id") == prop_id:
                return prop.get("value") or ""
    return ""


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _praca(offer: dict) -> str:
    desc = offer.get("auction", {}).get("judicialPracaDescription") or ""
    return desc


def _ocupado(offer: dict) -> str:
    situacao = _template_value(offer, "situacao").lower()
    if "desocupado" in situacao:
        return "nao"
    if "ocupado" in situacao:
        return "sim"
    return "desconhecido"


def _parse_endereco_completo(texto: str) -> tuple[str, str]:
    """'Rua X n° 160 - Bela Vista - São Paulo/SP, 01308-010' -> (endereco, bairro)."""
    partes = [p.strip() for p in texto.split(" - ")]
    if len(partes) >= 3:
        return partes[0], partes[1]
    if partes:
        return partes[0], ""
    return "", ""


def _to_listing(offer: dict) -> Listing:
    # CUIDADO: offer["auction"]["address"] é o endereço do LEILOEIRO, não do imóvel.
    # A localização real do imóvel vem de product.location (bairro só dá pra tentar
    # extrair do texto livre em product.template "endereco").
    location = offer.get("product", {}).get("location", {}) or {}
    detail = offer.get("offerDetail", {}) or {}
    area = _to_float(_template_value(offer, "areatotal").replace(".", "").replace(",", "."))
    endereco_completo = _template_value(offer, "endereco")
    endereco, bairro = _parse_endereco_completo(endereco_completo)

    cidade_uf = location.get("city", "")  # formato "São Paulo - SP"
    cidade, _, estado = cidade_uf.rpartition(" - ")
    cidade = cidade.strip() or cidade_uf

    return Listing(
        fonte="sold",
        id_no_site=str(offer["id"]),
        titulo=offer.get("product", {}).get("shortDesc", "")[:200],
        endereco=endereco,
        bairro=bairro,
        cidade=cidade,
        estado=estado.strip(),
        # a categoria (Imóveis Residenciais / Comerciais / Rurais / Terrenos e Lotes)
        # é bem mais útil pra filtrar do que o productType genérico ("Imóveis" pra tudo)
        tipo_imovel=(offer.get("product", {}).get("subCategory", {}) or {}).get("category", {}).get("description", ""),
        area_m2=area,
        # "price" (== referenceValue/directSaleValue) é o preço de referência/venda direta;
        # o valor que de fato se paga arrematando no leilão é o lance mínimo atual.
        valor_avaliacao=_to_float(detail.get("referenceValue") or detail.get("directSaleValue") or offer.get("price")),
        valor_lance_atual=_to_float(detail.get("currentMinBid") or detail.get("reservedPrice") or detail.get("initialBidValue") or offer.get("price")),
        praca=_praca(offer),
        data_leilao=offer.get("endDate", ""),
        ocupado=_ocupado(offer),
        url=f"https://www.sold.com.br/oferta/imovel-{offer['id']}",
        imagem_url=offer.get("product", {}).get("thumbnailUrl", ""),
    )


def buscar(estados: Optional[Iterable[str]] = None, max_paginas: int = 20) -> list[Listing]:
    """Busca todos os imóveis em leilão na Sold, opcionalmente filtrando por UF."""
    estados_upper = {e.upper() for e in estados} if estados else None
    listings: list[Listing] = []
    pagina = 1
    while pagina <= max_paginas:
        params = {
            "filter": FILTER,
            "geoLocation": "true",
            "locale": "pt_BR",
            "orderBy": "price:desc",
            "pageNumber": pagina,
            "pageSize": PAGE_SIZE,
            "portalId": "[2,15]",
            "requestOrigin": "store",
            "searchType": "opened",
            "timeZoneId": "America/Sao_Paulo",
        }
        resp = requests.get(API_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        offers = data.get("offers", [])
        if not offers:
            break
        for offer in offers:
            try:
                listing = _to_listing(offer)
            except Exception:
                continue
            if estados_upper and listing.estado.upper() not in estados_upper:
                continue
            listings.append(listing)
        if pagina * PAGE_SIZE >= data.get("total", 0):
            break
        pagina += 1
    return listings


if __name__ == "__main__":
    result = buscar(estados=["SP"], max_paginas=2)
    print(f"{len(result)} imóveis encontrados")
    for l in result[:5]:
        print(l.cidade, l.bairro, l.valor_lance_atual, l.valor_avaliacao, l.url)
