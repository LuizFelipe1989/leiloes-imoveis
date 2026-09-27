"""
Scraper do Smart Leilões Caixa (smartleiloescaixa.com.br).

Portal (Angular SSR) que revende os mesmos imóveis oficiais da Caixa
(`origemIntegracao: "CAIXA"`, `hdnImovel` = número oficial do imóvel na
Caixa — o MESMO id que já usamos como `id_no_site` da fonte "caixa"), mas com
uma API própria (App Engine, achada inspecionando o bundle JS do site — não é
documentada) que expõe campos que a Caixa não publica no CSV oficial:
quartos, garagem, coordenadas (lat/lng), condições de pagamento
(FGTS/consórcio/financiamento/parcelamento), pendência de ação judicial e a
modalidade de venda original (1º/2º leilão, licitação aberta, venda online,
venda direta).

Por isso este módulo NÃO devolve `Listing` pra inserção via upsert normal:
como os registros aqui são majoritariamente os MESMOS imóveis que o
scraper/caixa.py já traz (confirmado: ~90% de sobreposição por hdnImovel numa
amostra SP/MG), tratar isso como fonte nova duplicaria a base. Em vez disso,
devolve dicts crus pra `cli.py` decidir: enriquecer o imóvel `caixa:<id>` já
existente (a maioria dos casos) ou, se o hdnImovel não existir ainda na nossa
base, inserir como imóvel novo da fonte "caixa" mesmo (é Caixa de verdade,
só que descoberto por aqui).
"""

import sys
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = "https://api-dot-site-smart-leiloes.rj.r.appspot.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; leiloes-imoveis-bot/1.0)",
    "Content-Type": "application/json",
    "Origin": "https://smartleiloescaixa.com.br",
}

# "Venda Direta" da Caixa é preço fixo (primeira proposta igual/acima do
# mínimo vence) — mapeia pro nosso "venda_direta". Os demais (leilão 1ª/2ª
# praça, licitação aberta, venda online) são disputados por maior lance/
# proposta, mapeiam pro nosso "leilao".
MODO_VENDA_PARA_MODALIDADE = {"venda direta": "venda_direta"}


def _int_ou_none(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def buscar_bruto(estados: Optional[list[str]] = None, tamanho_pagina: int = 1000) -> list[dict]:
    """Busca todos os registros (paginando por offset) pros estados informados
    (default: todos). Devolve os dicts crus da API, um por imóvel."""
    body: dict = {}
    if estados:
        body["estados"] = [e.upper() for e in estados]

    registros: list[dict] = []
    offset = 0
    while True:
        payload = {**body, "max": tamanho_pagina, "offset": offset}
        resp = requests.post(f"{BASE_URL}/imovel/busca", json=payload, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        pagina = resp.json().get("records", [])
        if not pagina:
            break
        registros.extend(pagina)
        offset += tamanho_pagina
        if len(pagina) < tamanho_pagina:
            break
    return registros


def registro_para_campos(registro: dict) -> dict:
    """Converte um registro cru da API num dict pronto pra uso em cli.py:
    id_no_site (hdnImovel), campos de enriquecimento e os campos "básicos"
    (endereco/bairro/etc.) pra caso precise inserir como imóvel novo."""
    modo_venda = (registro.get("modoVenda") or "").strip()
    coordenadas = registro.get("coordenadas") or {}
    return {
        "id_no_site": str(registro.get("hdnImovel") or "").strip(),
        "modo_venda_original": modo_venda,
        "modalidade": MODO_VENDA_PARA_MODALIDADE.get(modo_venda.lower(), "leilao"),
        # enriquecimento
        "quartos": _int_ou_none(registro.get("quartos")),
        "garagem": _int_ou_none(registro.get("garagem")),
        "aceita_fgts": registro.get("aceitaFGTS"),
        "aceita_consorcio": registro.get("aceitaConsorcio"),
        "aceita_financiamento": registro.get("aceitaFinanciamentoHabitacional"),
        "aceita_parcelamento": registro.get("aceitaParcelamento"),
        "tem_acao_judicial": registro.get("temAcaoJudicial"),
        "lat": coordenadas.get("lat"),
        "lng": coordenadas.get("lng"),
        # básicos, só usados se o imóvel ainda não existir na nossa base
        "titulo": registro.get("tipoImovel") or "",
        "endereco": registro.get("endereco") or "",
        "bairro": registro.get("bairro") or "",
        "cidade": (registro.get("cidade") or "").strip(),
        "estado": (registro.get("estado") or "").strip(),
        "tipo_imovel": registro.get("tipoImovel") or "",
        "area_m2": registro.get("areaPrivativa") or registro.get("areaTotal") or registro.get("areaTerreno"),
        "valor_avaliacao": registro.get("precoAvaliacao"),
        "valor_lance_atual": registro.get("precoVenda"),
        "praca": modo_venda,
        "url": registro.get("siteLeiloeiro") or "",
        "imagem_url": "",
    }


def buscar(estados: Optional[list[str]] = None) -> list[dict]:
    """Busca e já converte pra dicts prontos pra uso (ver registro_para_campos)."""
    return [registro_para_campos(r) for r in buscar_bruto(estados)]


if __name__ == "__main__":
    result = buscar(["SP", "MG"])
    print(f"{len(result)} imóveis encontrados")
    for r in result[:5]:
        print(r["id_no_site"], "|", r["modalidade"], "|", r["cidade"], r["estado"], "|",
              r["quartos"], "quartos,", r["garagem"], "vagas |", r["tem_acao_judicial"])
