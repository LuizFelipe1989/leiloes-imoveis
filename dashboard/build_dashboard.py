"""Gera dashboard.html estático a partir do banco de oportunidades."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calculator import InvestmentInputs, calcular
from db import connect, listar_com_ultima_analise
from scraper.filters import categoria_imovel

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


def build():
    with connect() as conn:
        rows = [_row_to_dict(r) for r in listar_com_ultima_analise(conn)]

    total = len(rows)
    analisados = [r for r in rows if r.get("roi_anualizado_pct") is not None]
    oportunidades = [r for r in analisados if (r.get("roi_anualizado_pct") or 0) >= 20]
    por_fonte = {}
    por_categoria = {}
    for r in rows:
        por_fonte[r["fonte"]] = por_fonte.get(r["fonte"], 0) + 1
        por_categoria[r["categoria"]] = por_categoria.get(r["categoria"], 0) + 1

    resumo = {
        "total": total,
        "analisados": len(analisados),
        "oportunidades": len(oportunidades),
        "por_fonte": por_fonte,
        "por_categoria": por_categoria,
        "gerado_em": max((r["ultima_atualizacao"] for r in rows), default=None),
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for placeholder, fname in FONT_PLACEHOLDERS:
        template = template.replace(placeholder, (FONTS_DIR / f"{fname}.b64").read_text().strip())
    html = template.replace(
        "/*__DADOS__*/",
        f"const DADOS = {json.dumps(rows, ensure_ascii=False)};\nconst RESUMO = {json.dumps(resumo, ensure_ascii=False)};",
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {OUTPUT_PATH} ({total} imóveis, {len(oportunidades)} oportunidades)")


if __name__ == "__main__":
    build()
