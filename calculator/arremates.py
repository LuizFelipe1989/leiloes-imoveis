"""
Busca de arremates reais — leilões de imóveis já CONCLUÍDOS e com lance
vencedor de verdade — pra complementar a estimativa de venda com "por quanto
imóveis parecidos foram efetivamente arrematados recentemente na região".

Isso é diferente dos comparáveis do QuintoAndar (calculator/mercado.py), que
são preço PEDIDO no mercado aberto (imóveis à venda, não necessariamente
vendidos). Arremate é preço PAGO de fato, no próprio universo de leilão —
sinal mais direto de "quanto vale arrematar algo parecido", mas também mais
raro (só existe se um imóvel parecido foi a leilão e teve disputa).

Usa a mesma API pública da Superbid que a Sold (scraper/sold.py), só que com
searchType=closed + hasBids:true no filtro — só traz leilão que fechou com
alguém realmente dando lance, não os que venceram o prazo sem ninguém
aparecer (que não dizem nada sobre valor de mercado).

A API não permite filtrar por bairro/cidade no servidor, então a estratégia é:
baixar um lote nacional recente (as N mais recentes primeiro) UMA VEZ por
execução do `cli.py analisar`, e indexar em memória por (estado, cidade,
grupo) pra consultar rápido pra cada imóvel sendo analisado — bem mais barato
que uma chamada de API por imóvel/bairro.
"""

import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calculator.mercado import grupo_tipo
from scraper.sold import _template_value

API_URL = "https://offer-query.superbid.net/offers/"
# hasBids:true é o que garante que teve disputa de verdade (não só o prazo vencendo sem lance)
FILTER = "product.productType.description:imoveis;stores.id:[1161,1741];hasBids:true"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; leiloes-imoveis-bot/1.0)"}
TIMEOUT = 20


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return texto.strip().upper()


def buscar_arremates_recentes(max_paginas: int = 8, page_size: int = 100) -> list[dict]:
    """Busca leilões de imóveis concluídos com lance vencedor, mais recentes primeiro."""
    resultados = []
    for pagina in range(1, max_paginas + 1):
        params = {
            "filter": FILTER, "geoLocation": "true", "locale": "pt_BR",
            # "endDate:desc" parece não funcionar pra searchType=closed (a API devolve
            # os leilões mais antigos primeiro, de 2021, sem dar erro) — updateAt:desc
            # é o que de fato traz os arremates mais recentes primeiro.
            "orderBy": "updateAt:desc", "pageNumber": pagina, "pageSize": page_size,
            "portalId": "[2,15]", "requestOrigin": "store", "searchType": "closed",
            "timeZoneId": "America/Sao_Paulo",
        }
        try:
            resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            break
        offers = data.get("offers", [])
        if not offers:
            break
        for o in offers:
            location = o.get("product", {}).get("location", {}) or {}
            cidade_uf = location.get("city", "")
            cidade, _, estado = cidade_uf.rpartition(" - ")
            cidade = (cidade or cidade_uf).strip()
            estado = estado.strip()
            valor = _to_float(o.get("price"))
            if not (cidade and estado and valor):
                continue
            # subCategory.description é o nível fino ("Casas", "Apartamentos", "Coberturas"...);
            # subCategory.category.description seria só o nível grosso ("Imóveis Residenciais"),
            # fino demais pra classificar em casa vs. apartamento, então usa o primeiro.
            subcategoria_desc = (o.get("product", {}).get("subCategory", {}) or {}).get("description", "")
            area = _to_float(_template_value(o, "areatotal"))
            resultados.append({
                "cidade": cidade,
                "estado": estado,
                "grupo": grupo_tipo(subcategoria_desc),
                "valor": valor,
                "area_m2": area,
                "preco_m2": (valor / area) if area else None,
                "data": o.get("endDate", ""),
                "endereco": o.get("product", {}).get("shortDesc", "")[:150],
            })
        if pagina * page_size >= data.get("total", 0):
            break
    return resultados


def indexar_por_regiao(arremates: list[dict]) -> dict:
    """Agrupa por (estado, cidade, grupo) normalizados — assume que `arremates`
    já vem ordenado por data desc (mais recente primeiro), como `buscar_arremates_recentes` devolve."""
    indice: dict = {}
    for a in arremates:
        if not a["grupo"]:
            continue
        chave = (_normalizar(a["estado"]), _normalizar(a["cidade"]), a["grupo"])
        indice.setdefault(chave, []).append(a)
    return indice


def resumo_regiao(indice: dict, estado: str, cidade: str, tipo_imovel: str) -> Optional[dict]:
    """Resumo dos arremates recentes pra (estado, cidade, grupo do tipo_imovel) —
    o último arremate (valor/data/endereço) em destaque, mais a mediana de R$/m²
    da amostra como contexto. None se não achar nenhum arremate parecido."""
    grupo = grupo_tipo(tipo_imovel)
    if not grupo:
        return None
    chave = (_normalizar(estado), _normalizar(cidade), grupo)
    itens = indice.get(chave)
    if not itens:
        return None
    ultimo = itens[0]
    precos_m2 = sorted(a["preco_m2"] for a in itens if a["preco_m2"])
    mediana_m2 = None
    if precos_m2:
        n = len(precos_m2)
        mediana_m2 = precos_m2[n // 2] if n % 2 else (precos_m2[n // 2 - 1] + precos_m2[n // 2]) / 2
    return {
        "ultimo_valor": ultimo["valor"],
        "ultimo_data": ultimo["data"],
        "ultimo_endereco": ultimo["endereco"],
        "ultimo_preco_m2": ultimo["preco_m2"],
        "mediana_preco_m2": mediana_m2,
        "n_amostra": len(itens),
    }


if __name__ == "__main__":
    dados = buscar_arremates_recentes(max_paginas=3)
    print(f"{len(dados)} arremates com lance vencedor encontrados")
    indice = indexar_por_regiao(dados)
    print(f"{len(indice)} combinações (estado, cidade, grupo) distintas")
    for chave in list(indice)[:5]:
        print(chave, len(indice[chave]))
    r = resumo_regiao(indice, "SP", "São Paulo", "Apartamento")
    print("resumo SP/São Paulo/Apartamento:", r)
