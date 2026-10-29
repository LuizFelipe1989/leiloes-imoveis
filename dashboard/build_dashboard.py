"""Gera dashboard.html estático a partir do banco de oportunidades."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calculator import InvestmentInputs, calcular
from calculator.mercado import grupo_tipo
from db import connect, listar_com_ultima_analise
from scraper.filters import categoria_imovel

TIPO_LABEL = {"casa": "Casa", "apartamento": "Apartamento"}

OUTPUT_PATH = Path(__file__).parent.parent / "dashboard.html"
DETALHES_DIR = Path(__file__).parent.parent / "detalhes"
TEMPLATE_PATH = Path(__file__).parent / "template.html"
FONTS_DIR = Path(__file__).parent.parent / "fonts"

# o HTML leva só o que a tabela mostra; o resto (premissas, resultado do cálculo,
# informações adicionais) vai pra detalhes/<modalidade>-<UF>.json e é baixado
# quando alguém clica em "ver cálculo" — era ~60% do tamanho do arquivo.
CAMPOS_TABELA = [
    "id", "modalidade", "fonte", "banco", "categoria", "tipo", "estado", "cidade",
    "bairro", "valor_lance_atual", "valor_avaliacao", "desconto_pct",
    "roi_anualizado_pct", "status", "url",
]
CAMPOS_DETALHE = [
    "premissas", "detalhe", "praca", "quartos", "garagem", "aceita_fgts",
    "aceita_financiamento", "aceita_consorcio", "aceita_parcelamento",
    "tem_acao_judicial", "lat", "lng",
]

FONT_PLACEHOLDERS = [
    ("__FONT_SERIF_600__", "source-serif-600"),
    ("__FONT_SANS_400__", "plex-sans-400"),
    ("__FONT_SANS_500__", "plex-sans-500"),
    ("__FONT_SANS_600__", "plex-sans-600"),
    ("__FONT_MONO_400__", "plex-mono-400"),
    ("__FONT_MONO_500__", "plex-mono-500"),
]


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["categoria"] = categoria_imovel(d.get("tipo_imovel"))
    d["tipo"] = TIPO_LABEL.get(grupo_tipo(d.get("tipo_imovel"))) or d["categoria"]
    inputs_json = d.pop("inputs_json", None)
    d["premissas"] = None
    d["detalhe"] = None
    if inputs_json:
        try:
            inputs_dict = json.loads(inputs_json)
            inputs = InvestmentInputs(**inputs_dict)
            resultado = calcular(inputs)
            d["premissas"] = inputs_dict
            d["detalhe"] = asdict(resultado)
        except Exception:
            pass  # análise antiga/incompatível — mostra só o que já está salvo nas colunas
    return d


def _montar_resumo(rows: list[dict]) -> dict:
    analisados = [r for r in rows if r.get("roi_anualizado_pct") is not None]
    oportunidades = [r for r in analisados if (r.get("roi_anualizado_pct") or 0) >= 20]
    por_fonte = {}
    por_categoria = {}
    for r in rows:
        por_fonte[r["fonte"]] = por_fonte.get(r["fonte"], 0) + 1
        por_categoria[r["categoria"]] = por_categoria.get(r["categoria"], 0) + 1
    return {
        "total": len(rows),
        "analisados": len(analisados),
        "oportunidades": len(oportunidades),
        "por_fonte": por_fonte,
        "por_categoria": por_categoria,
        "gerado_em": max((r["ultima_atualizacao"] for r in rows), default=None),
    }


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def chave_detalhe(row: dict) -> str:
    return f"{row.get('modalidade') or 'leilao'}-{row.get('estado') or 'NA'}"


def _gravar_detalhes(rows: list[dict]) -> int:
    """Grava um JSON por modalidade+UF ({id: campos do detalhe}) e apaga os
    arquivos de execuções anteriores que não foram regerados."""
    DETALHES_DIR.mkdir(exist_ok=True)
    grupos: dict[str, dict] = {}
    for r in rows:
        entrada = {k: r[k] for k in CAMPOS_DETALHE if r.get(k) not in (None, "", 0, False)}
        if entrada:
            grupos.setdefault(chave_detalhe(r), {})[str(r["id"])] = entrada
    for chave, conteudo in grupos.items():
        (DETALHES_DIR / f"{chave}.json").write_text(_json(conteudo), encoding="utf-8")
    for antigo in DETALHES_DIR.glob("*.json"):
        if antigo.stem not in grupos:
            antigo.unlink()
    return len(grupos)


def build():
    with connect() as conn:
        rows = [_row_to_dict(r) for r in listar_com_ultima_analise(conn)]

    # cada modalidade (leilão / venda online / venda direta) tem seu próprio
    # resumo — o seletor de perfil no dash troca qual desses é exibido, sem
    # trocar de URL. MODALIDADES fixa a ordem/presença mesmo se uma delas
    # não tiver nenhum imóvel ainda (option do <select> não pode sumir).
    MODALIDADES = ["leilao", "venda_online", "venda_direta"]
    resumos = {
        modalidade: _montar_resumo([r for r in rows if r.get("modalidade", "leilao") == modalidade])
        for modalidade in MODALIDADES
    }
    total = len(rows)
    oportunidades_total = sum(resumos[m]["oportunidades"] for m in MODALIDADES)

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder, fname in FONT_PLACEHOLDERS:
        template = template.replace(placeholder, (FONTS_DIR / f"{fname}.b64").read_text().strip())
    html = template.replace(
        "/*__DADOS__*/",
        f"const DADOS = {_json([{k: r.get(k) for k in CAMPOS_TABELA} for r in rows])};\n"
        f"const RESUMOS = {_json(resumos)};",
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    n_arquivos = _gravar_detalhes(rows)
    resumo_fontes = ", ".join(f"{resumos[m]['total']} {m}" for m in MODALIDADES)
    print(f"Dashboard gerado em {OUTPUT_PATH} ({total} imóveis — {resumo_fontes}, "
          f"{oportunidades_total} oportunidades); detalhes em {n_arquivos} arquivos")


if __name__ == "__main__":
    build()
