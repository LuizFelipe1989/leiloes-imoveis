"""
Scraper de imóveis do Banco do Brasil via Meu Arremate Leilões
(meuarremateleiloes.com.br, também conhecido como leilaoimovel.com.br).

O BB não tem portal próprio de venda de imóveis como a Caixa — os imóveis dele
são vendidos via leiloeiros terceirizados. Esse agregador reúne tanto os
leilões extrajudiciais quanto as vendas diretas do BB (e, no fundo, de vários
bancos — dá pra reusar esse scraper pra outros bancos trocando `banco_slug`).

Site é HTML estático simples, sem proteção anti-bot — dá pra usar requests
direto.
"""

import re
import sys
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.store import Listing
from scraper.enderecos import extrair_bairro

BASE_URL = "https://www.meuarremateleiloes.com.br"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; leiloes-imoveis-bot/1.0)"}


def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"([\d.]+,\d{2})", text)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def _split_titulo(titulo: str) -> tuple[str, str, str]:
    """
    "Terreno em Leilão em Araras / SP" -> ("Terreno", "Araras", "SP")
    "Apartamento Banco do Brasil em Sabará / MG" -> ("Apartamento", "Sabará", "MG")
    "Apartamentos Banco do Brasil no Rio De Janeiro / RJ" -> ("Apartamentos", "Rio De Janeiro", "RJ")

    A cidade vem sempre depois da ÚLTIMA preposição ("em"/"no") antes do "/ UF" —
    isso evita pegar o "em" que aparece no meio do título (ex: "em Leilão", "Banco do Brasil em").
    """
    m = re.search(r"/\s*([A-Z]{2})\s*$", titulo)
    if not m:
        return titulo.strip(), "", ""
    uf = m.group(1)
    antes = titulo[: m.start()].rstrip()
    idx = max(antes.rfind(" em "), antes.rfind(" no "))
    tipo = (antes if idx == -1 else antes[:idx]).strip()
    cidade = "" if idx == -1 else antes[idx + 4 :].strip()
    # remove sufixos que não fazem parte do tipo em si (variam por leiloeiro/banco)
    tipo = re.sub(r"\s+(em\s+Leil[ãa]o|Banco do Brasil)\s*$", "", tipo, flags=re.IGNORECASE).strip()
    return tipo, cidade, uf


def _id_da_url(href: str) -> str:
    m = re.search(r"-(\d+)$", href.rstrip("/"))
    return m.group(1) if m else href


def _parse_card(card) -> Optional[Listing]:
    link = card.select_one("a.Link_Redirecter[href*='/imovel/']")
    if not link:
        return None
    href = link.get("href", "")
    url = href if href.startswith("http") else BASE_URL + href

    titulo_el = card.select_one(".address b")
    titulo = titulo_el.get_text(strip=True) if titulo_el else ""
    titulo = re.sub(r"\s*-\s*\d+$", "", titulo)  # remove o " - <id>" do final

    tipo, cidade, estado = _split_titulo(titulo)

    endereco_el = card.select_one(".address span")
    endereco_completo = endereco_el.get_text(strip=True) if endereco_el else ""
    endereco, bairro = extrair_bairro(endereco_completo, cidade)

    lance_el = card.select_one(".discount-price")
    avaliacao_el = card.select_one(".last-price")
    valor_lance = _parse_money(lance_el.get_text(strip=True)) if lance_el else None
    valor_avaliacao = _parse_money(avaliacao_el.get_text(strip=True)) if avaliacao_el else None
    if valor_lance is None:
        # Venda Direta: só tem um preço, sem desconto (".price" dentro de .prices)
        preco_el = card.select_one(".prices .price")
        valor_lance = _parse_money(preco_el.get_text(strip=True)) if preco_el else None
        valor_avaliacao = valor_lance

    categorias = [a.get_text(strip=True) for a in card.select(".categories a")]
    ocupado = "desconhecido"
    for c in categorias:
        cl = c.lower()
        if "desocupado" in cl:
            ocupado = "nao"
        elif "ocupado" in cl or "locado" in cl:
            ocupado = "sim"
    modalidades = [c for c in categorias if c.lower() not in ("desocupado", "ocupado", "locado")]

    img_el = card.select_one("img")
    imagem_url = img_el.get("src", "") if img_el else ""

    return Listing(
        fonte="bancodobrasil",
        id_no_site=_id_da_url(href),
        titulo=titulo,
        endereco=endereco,
        bairro=bairro,
        cidade=cidade,
        estado=estado,
        tipo_imovel=tipo,
        valor_avaliacao=valor_avaliacao,
        valor_lance_atual=valor_lance,
        praca=", ".join(modalidades),
        ocupado=ocupado,
        url=url,
        imagem_url=imagem_url,
    )


def _parse_listing_page(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.place-box")
    listings = []
    for card in cards:
        listing = _parse_card(card)
        if listing:
            listings.append(listing)
    return listings


def buscar(
    estados: Optional[list[str]] = None,
    banco_slug: str = "banco-do-brasil",
    max_paginas: int = 30,
) -> list[Listing]:
    """Busca imóveis do banco informado (default: Banco do Brasil) no agregador.

    O site não tem filtro de UF na URL, então quando `estados` é passado o
    filtro é feito depois de buscar tudo (client-side, como no scraper da Sold).
    """
    estados_upper = {e.upper() for e in estados} if estados else None
    listings: list[Listing] = []
    for pagina in range(1, max_paginas + 1):
        url = f"{BASE_URL}/banco_leilao_de_imoveis/{banco_slug}"
        params = {"pag": pagina} if pagina > 1 else {}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        pagina_listings = _parse_listing_page(resp.text)
        if not pagina_listings:
            break
        listings.extend(pagina_listings)
    if estados_upper:
        listings = [l for l in listings if l.estado.upper() in estados_upper]
    return listings


if __name__ == "__main__":
    result = buscar(max_paginas=2)
    print(f"{len(result)} imóveis encontrados")
    for l in result[:8]:
        print(l.tipo_imovel, "|", l.cidade, l.estado, "|", l.valor_lance_atual, "/", l.valor_avaliacao, "|", l.ocupado, "|", l.url)
