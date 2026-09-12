"""Gera dashboard.html estático a partir do banco de oportunidades."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import connect, listar_com_ultima_analise

OUTPUT_PATH = Path(__file__).parent.parent / "dashboard.html"
TEMPLATE_PATH = Path(__file__).parent / "template.html"


def _row_to_dict(row) -> dict:
    d = dict(row)
    return d


def build():
    with connect() as conn:
        rows = [_row_to_dict(r) for r in listar_com_ultima_analise(conn)]

    total = len(rows)
    analisados = [r for r in rows if r.get("roi_anualizado_pct") is not None]
    oportunidades = [r for r in analisados if (r.get("roi_anualizado_pct") or 0) >= 20]
    por_fonte = {}
    for r in rows:
        por_fonte[r["fonte"]] = por_fonte.get(r["fonte"], 0) + 1

    resumo = {
        "total": total,
        "analisados": len(analisados),
        "oportunidades": len(oportunidades),
        "por_fonte": por_fonte,
        "gerado_em": rows[0]["ultima_atualizacao"] if rows else None,
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = template.replace(
        "/*__DADOS__*/",
        f"const DADOS = {json.dumps(rows, ensure_ascii=False)};\nconst RESUMO = {json.dumps(resumo, ensure_ascii=False)};",
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {OUTPUT_PATH} ({total} imóveis, {len(oportunidades)} oportunidades)")


if __name__ == "__main__":
    build()
