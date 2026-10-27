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

IMPORTANTE: rode `buscar()` num processo Python que NÃO tenha importado
`requests` antes — por algum motivo (nunca totalmente investigado; suspeita é
conflito de SSL entre urllib3 e o processo do Chromium do Playwright) ter
`requests` importado no mesmo processo faz o `page.goto()` falhar com
"Target page, context or browser has been closed" especificamente nesse
domínio. `cli.py` por isso roda esse módulo como subprocesso isolado (`python
scraper/caixa.py <UF> <UF> ...` imprime os imóveis em JSON no stdout) em vez
de chamar `caixa.buscar()` direto — todos os outros scrapers já usam
`requests`, então qualquer import compartilhado do mesmo processo reproduz o bug.
"""

import base64
import csv
import io
import json
import re
import sys
import time
from dataclasses import asdict
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

# timeout de 60s no próprio fetch: sem isso um download pendurado deixava o
# subprocesso rodando até estourar o limite de 300s do cli.py (visto em 18/10).
_FETCH_AS_BASE64_JS = """
async (url) => {
    const resp = await fetch(url, {signal: AbortSignal.timeout(60000)});
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


def _area_da_descricao(descricao: str) -> Optional[float]:
    # "Casa, 130.00 de área total, 45.32 de área privativa, 239.90 de área do terreno, ..."
    # área privativa é a mais comparável com o "área útil" que os sites de mercado reportam;
    # cai pra área total (casas costumam não ter "privativa" preenchida) e por fim terreno.
    def valor(rotulo: str) -> Optional[float]:
        m = re.search(rf"([\d.]+)\s*de\s*{rotulo}", descricao or "")
        if not m:
            return None
        try:
            v = float(m.group(1))
        except ValueError:
            return None
        return v if v > 0 else None

    return valor("área privativa") or valor("área total") or valor("área do terreno")


def _tipo_da_descricao(descricao: str) -> str:
    # "Casa, 130.00 de área total, ..." -> "Casa"; "Terreno Comercial, 500 m2" -> mantém como está
    return descricao.split(",")[0].strip() if descricao else ""


# Coluna "Modalidade de venda" do CSV oficial (100% preenchida; valores vistos em
# 27/10: "Leilão SFI - Edital Único", "Licitação Aberta", "Venda Online",
# "Venda Direta Online"). Validado contra o cabeçalho de 50 páginas de detalhe
# no site (Leilão SFI/Licitação Aberta têm leiloeiro, Venda Online e Compra
# Direta não). Leilão e Licitação Aberta cobram 5% de comissão de leiloeiro;
# Venda Online (disputa com cronômetro no portal) e Venda Direta não.
MODALIDADE_CSV = {
    "leilão sfi - edital único": ("leilao", ""),          # 1º/2º leilão: Smart Leilões refina o `praca`
    "licitação aberta": ("leilao", "Licitação Aberta"),
    "venda online": ("venda_online", "Venda Online"),
    "venda direta online": ("venda_direta", "Venda Direta"),
}


def _modalidade_do_csv(rotulo: str) -> tuple[str, str]:
    """(modalidade, praca) a partir do rótulo do CSV. Rótulo desconhecido
    devolve modalidade "" — o upsert então NÃO mexe na modalidade já salva
    (melhor manter a classificação anterior do que chutar leilão e cobrar
    comissão de leiloeiro de um imóvel que talvez nem tenha)."""
    chave = (rotulo or "").strip().lower()
    if chave in MODALIDADE_CSV:
        return MODALIDADE_CSV[chave]
    if chave not in _ROTULOS_AVISADOS:  # um aviso por rótulo, não um por imóvel
        _ROTULOS_AVISADOS.add(chave)
        print(f"[caixa] AVISO: 'Modalidade de venda' desconhecida no CSV: {rotulo!r}", file=sys.stderr)
    return "", ""


_ROTULOS_AVISADOS: set = set()


def _row_to_listing(row: dict) -> Optional[Listing]:
    id_imovel = row.get("N° do imóvel", "").strip()
    if not id_imovel:
        return None
    descricao = row.get("Descrição", "")
    modalidade, praca = _modalidade_do_csv(row.get("Modalidade de venda", ""))
    return Listing(
        fonte="caixa",
        id_no_site=id_imovel,
        banco="Caixa",
        titulo=descricao[:200],
        endereco=row.get("Endereço", "").strip(),
        bairro=row.get("Bairro", "").strip(),
        cidade=row.get("Cidade", "").strip(),
        estado=row.get("UF", "").strip(),
        tipo_imovel=_tipo_da_descricao(descricao),
        area_m2=_area_da_descricao(descricao),
        valor_avaliacao=_to_float(row.get("Valor de avaliação", "")),
        valor_lance_atual=_to_float(row.get("Preço", "")),
        modalidade=modalidade,
        praca=praca,
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
            # "load", NÃO "networkidle": a página de busca dispara pixels de
            # analytics de terceiros (Microsoft Clarity/Bing, DoubleClick) que
            # às vezes ficam pendurados pra sempre — networkidle (zero
            # requisições em andamento) nunca chega e estourava os 30s mesmo com
            # a página já pronta em ~1,6s e a sessão da Caixa já estabelecida.
            # Medido em 27/10: 8/8 coletas completas com "load", enquanto
            # networkidle falhava em sequência na mesma hora.
            page.goto(f"{BASE_URL}/sistema/busca-imovel.asp", wait_until="load", timeout=60000)
            for uf in estados:
                csv_url = f"{BASE_URL}/listaweb/Lista_imoveis_{uf.upper()}.csv"
                rows = _baixar_csv(page, uf, csv_url)
                for row in rows:
                    listing = _row_to_listing(row)
                    if listing:
                        listings.append(listing)
        finally:
            browser.close()
    return listings


def _baixar_csv(page, uf: str, csv_url: str, tentativas: int = 3) -> list[dict]:
    """Baixa e faz parse do CSV de uma UF. Se não conseguir, LEVANTA erro em vez
    de seguir sem essa UF: devolver só parte dos estados como se fosse sucesso
    fazia o cli.py achar que os imóveis do estado faltante tinham sumido da
    fonte (e fechá-los) — e o aviso ia só pro stderr, que o cli.py descarta
    quando o subprocesso termina com código 0."""
    erro = "sem detalhe"
    for tentativa in range(1, tentativas + 1):
        try:
            result = page.evaluate(_FETCH_AS_BASE64_JS, csv_url)
            if result["status"] == 200 and result["base64"]:
                rows = _parse_csv(base64.b64decode(result["base64"]))
                if rows:
                    return rows
                erro = "CSV veio vazio"
            else:
                erro = f"HTTP {result['status']}"
        except Exception as e:  # timeout do fetch, conexão caída etc.
            erro = str(e).splitlines()[0][:120]
        print(f"[caixa] {uf}: tentativa {tentativa}/{tentativas} do CSV falhou ({erro})", file=sys.stderr)
        if tentativa < tentativas:
            time.sleep(3 * tentativa)
    raise RuntimeError(f"não foi possível baixar a lista de {uf} da Caixa: {erro}")


if __name__ == "__main__":
    # modo CLI: imprime só JSON no stdout (pra `cli.py` rodar isso via subprocess
    # e conseguir reconstruir os Listings) — qualquer log/erro vai pro stderr.
    ufs_arg = sys.argv[1:] or ["SP"]
    if ufs_arg == ["--debug"] or "--debug" in ufs_arg:
        ufs_arg = [u for u in ufs_arg if u != "--debug"] or ["SP"]
        result = buscar(ufs_arg)
        print(f"{len(result)} imóveis encontrados", file=sys.stderr)
        for l in result[:5]:
            print(l.cidade, l.valor_lance_atual, l.valor_avaliacao, l.url, file=sys.stderr)
    else:
        result = buscar(ufs_arg)
        print(json.dumps([asdict(l) for l in result], ensure_ascii=False))
