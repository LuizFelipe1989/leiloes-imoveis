"""
Scraper da Caixa (venda-imoveis.caixa.gov.br).

A Caixa publica um CSV oficial com todos os imóveis à venda por estado em
https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_<UF>.csv — mas o
domínio tem proteção anti-bot (Radware Bot Manager) que bloqueia requests
simples (curl/requests) mesmo com headers de navegador, e também bloqueia
Chromium headless via Playwright. Só funciona abrindo o CSV de dentro de um
Chromium "de verdade" (headless=False), reaproveitando a sessão/cookies
estabelecidos ao navegar numa página normal do site primeiro.

Por isso esse scraper precisa do Playwright com uma janela de navegador real
(não roda em headless nem em servidor sem display).
"""

import base64
import csv
import io
import sys
from pathlib import Path
from typing import Iterable, Optional

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.store import Listing

BASE_URL = "https://venda-imoveis.caixa.gov.br"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_FETCH_AS_BASE64_JS = """
async (url) => {
    const resp = await fetch(url);
    if (resp.status !== 200) {
        return {status: resp.status, base64: null};
    }
    const buffer = await resp.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }
    return {status: resp.status, base64: btoa(binary)};
}
"""


def _to_float(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_csv(raw_bytes: bytes) -> list[dict]:
    text = raw_bytes.decode("cp1252", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    # primeira linha = título/data de geração, segunda linha = cabeçalho real
    reader = csv.reader(lines[1:], delimiter=";")
    header = [h.strip() for h in next(reader)]
    rows = []
    for row in reader:
        if len(row) < len(header):
            continue
        rows.append(dict(zip(header, row)))
    return rows


def _tipo_da_descricao(descricao: str) -> str:
    # "Casa, 130.00 de área total, ..." -> "Casa"; "Terreno Comercial, 500 m2" -> mantém como está
    return descricao.split(",")[0].strip() if descricao else ""


def _row_to_listing(row: dict) -> Optional[Listing]:
    id_imovel = row.get("N° do imóvel", "").strip()
    if not id_imovel:
        return None
    descricao = row.get("Descrição", "")
    return Listing(
        fonte="caixa",
        id_no_site=id_imovel,
        titulo=descricao[:200],
        endereco=row.get("Endereço", "").strip(),
        cidade=row.get("Cidade", "").strip(),
        estado=row.get("UF", "").strip(),
        tipo_imovel=_tipo_da_descricao(descricao),
        valor_avaliacao=_to_float(row.get("Valor de avaliação", "")),
        valor_lance_atual=_to_float(row.get("Preço", "")),
        praca="",
        data_leilao="",
        ocupado="desconhecido",
        url=row.get("Link de acesso", "").strip(),
    )


def buscar(estados: Iterable[str]) -> list[Listing]:
    """Baixa e faz parse do CSV oficial de imóveis à venda para cada UF informada.

    Requer um ambiente com display gráfico (não funciona headless nem em
    servidor puro) porque a Caixa bloqueia requisições sem uma sessão de
    navegador real.
    """
    listings: list[Listing] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(f"{BASE_URL}/sistema/busca-imovel.asp", wait_until="networkidle", timeout=30000)
            for uf in estados:
                csv_url = f"{BASE_URL}/listaweb/Lista_imoveis_{uf.upper()}.csv"
                result = page.evaluate(_FETCH_AS_BASE64_JS, csv_url)
                if result["status"] != 200 or not result["base64"]:
                    print(f"[caixa] falha ao baixar lista de {uf}: HTTP {result['status']}")
                    continue
                raw_bytes = base64.b64decode(result["base64"])
                rows = _parse_csv(raw_bytes)
                for row in rows:
                    listing = _row_to_listing(row)
                    if listing:
                        listings.append(listing)
        finally:
            browser.close()
    return listings


if __name__ == "__main__":
    result = buscar(["SP"])
    print(f"{len(result)} imóveis encontrados")
    for l in result[:5]:
        print(l.cidade, l.valor_lance_atual, l.valor_avaliacao, l.url)
