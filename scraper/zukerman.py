"""
Scraper da Zuk / Zukerman Leilões (portalzuk.com.br).

O site renderiza os cards de imóvel em HTML estático (sem proteção anti-bot),
então usamos requests + BeautifulSoup direto.

Limitação conhecida (v1): a listagem por estado carrega mais resultados via
"carregar mais" (AJAX/infinite scroll) que não foi mapeado ainda — por ora
pegamos só a primeira leva de cards retornada no HTML inicial. Para cobertura
maior, use `buscar_cidade` com as URLs de cidade específicas (mais precisas
para o caso de uso de localização/custo-benefício de qualquer forma).
"""

import re
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.store import Listing

BASE_URL = "https://www.portalzuk.com.br"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; leiloes-imoveis-bot/1.0)"}


def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"([\d.]+,\d{2})", text)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def _slug_id(href: str) -> str:
    # ".../37318-233025" -> usa o último segmento numérico como id
    return href.rstrip("/").split("/")[-1]


def _parse_card(card) -> Optional[Listing]:
    link = card.select_one("a[href*='/imovel/']")
    if not link:
        return None
    href = link.get("href", "")

    tipo_el = card.select_one(".card-property-price-lote")
    tipo = tipo_el.get_text(strip=True) if tipo_el else ""

    area_m2 = None
    area_el = card.select_one(".card-property-info-label")
    if area_el:
        m_area = re.search(r"([\d.,]+)\s*m", area_el.get_text(strip=True))
        if m_area:
            try:
                area_m2 = float(m_area.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                area_m2 = None

    addr = card.select_one("address.card-property-address")
    cidade = estado = bairro = endereco = ""
    if addr:
        spans = addr.find_all("span")
        if spans:
            # primeiro span: "Cidade / UF - Bairro" (cidade é um link)
            first_text = spans[0].get_text(" ", strip=True)
            m = re.match(r"(.+?)\s*/\s*([A-Z]{2})\s*-\s*(.+)", first_text)
            if m:
                cidade, estado, bairro = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if len(spans) > 1:
            endereco = spans[1].get_text(strip=True)

    news_el = card.select_one(".card-property-news")
    ocupado = "desconhecido"
    if news_el:
        texto = news_el.get_text(strip=True).lower()
        if "desocupado" in texto:
            ocupado = "nao"
        elif "ocupado" in texto:
            ocupado = "sim"

    precos = card.select("li.card-property-price[data-pracas], li.card-property-price")
    valor_lance = None
    praca = ""
    data_leilao = ""
    for li in precos:
        label_el = li.select_one(".card-property-price-label")
        valor_el = li.select_one(".card-property-price-value")
        data_el = li.select_one(".card-property-price-data")
        if valor_el and valor_lance is None:
            valor_lance = _parse_money(valor_el.get_text(strip=True))
            praca = label_el.get_text(strip=True) if label_el else ""
            data_leilao = data_el.get_text(strip=True) if data_el else ""

    img_el = card.select_one("img")
    imagem_url = img_el.get("src", "") if img_el else ""

    url = href if href.startswith("http") else BASE_URL + href

    return Listing(
        fonte="zukerman",
        id_no_site=_slug_id(href),
        titulo=tipo,
        endereco=endereco,
        bairro=bairro,
        cidade=cidade,
        estado=estado,
        tipo_imovel=tipo,
        area_m2=area_m2,
        valor_avaliacao=None,  # Zuk não expõe avaliação na listagem; preencher manualmente na análise
        valor_lance_atual=valor_lance,
        praca=praca,
        data_leilao=data_leilao,
        ocupado=ocupado,
        url=url,
        imagem_url=imagem_url,
    )


def _parse_listing_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.card-property.card_lotes_div")
    listings = []
    for card in cards:
        listing = _parse_card(card)
        if listing:
            listings.append(listing)
    return listings


def buscar(ufs: list[str]) -> list[Listing]:
    """Busca imóveis por UF (ex: ["SP", "RJ"]). Pega a primeira leva de resultados de cada estado."""
    listings: list[Listing] = []
    for uf in ufs:
        url = f"{BASE_URL}/leilao-de-imoveis/u/todos-imoveis/{uf.lower()}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        listings.extend(_parse_listing_page(resp.text))
    return listings


def buscar_cidade(uf: str, regiao: str, cidade_slug: str) -> list[Listing]:
    """Busca imóveis numa cidade específica, ex: buscar_cidade('sp', 'capital', 'sao-paulo')."""
    url = f"{BASE_URL}/leilao-de-imoveis/c/todos-imoveis/{uf.lower()}/{regiao}/{cidade_slug}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return _parse_listing_page(resp.text)


if __name__ == "__main__":
    result = buscar(["SP"])
    print(f"{len(result)} imóveis encontrados")
    for l in result[:5]:
        print(l.cidade, l.bairro, l.valor_lance_atual, l.praca, l.url)
