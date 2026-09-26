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
TEMPLATE_PATH = Path(__file__).parent / "template.html"
FONTS_DIR = Path(__file__).parent.parent / "fonts"

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


def build():
    with connect() as conn:
        rows = [_row_to_dict(r) for r in listar_com_ultima_analise(conn)]

    # cada modalidade (leilão / venda direta) tem seu próprio resumo — o
    # seletor de perfil no dash troca qual desses é exibido, sem trocar de URL
    resumos = {
        "leilao": _montar_resumo([r for r in rows if r.get("modalidade", "leilao") == "leilao"]),
        "venda_direta": _montar_resumo([r for r in rows if r.get("modalidade") == "venda_direta"]),
    }
    total = len(rows)
    oportunidades_total = resumos["leilao"]["oportunidades"] + resumos["venda_direta"]["oportunidades"]

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder, fname in FONT_PLACEHOLDERS:
        template = template.replace(placeholder, (FONTS_DIR / f"{fname}.b64").read_text().strip())
    html = template.replace(
        "/*__DADOS__*/",
        f"const DADOS = {json.dumps(rows, ensure_ascii=False)};\nconst RESUMOS = {json.dumps(resumos, ensure_ascii=False)};",
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {OUTPUT_PATH} ({total} imóveis, "
          f"{resumos['leilao']['total']} leilão / {resumos['venda_direta']['total']} venda direta, "
          f"{oportunidades_total} oportunidades)")


if __name__ == "__main__":
    build()
