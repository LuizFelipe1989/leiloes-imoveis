"""
CLI do projeto de leilões de imóveis.

Uso:
    python cli.py atualizar --fontes zukerman sold caixa --ufs SP
    python cli.py analisar
    python cli.py dashboard
"""

import argparse
from types import SimpleNamespace

from calculator import analise_rapida
from db import connect, save_analise, upsert_listings, listar_com_ultima_analise
from scraper import zukerman, sold, caixa


def cmd_atualizar(args):
    ufs = [u.upper() for u in args.ufs]
    todas = []

    if "zukerman" in args.fontes:
        print(f"[zukerman] buscando {ufs}...")
        try:
            r = zukerman.buscar(ufs)
            print(f"[zukerman] {len(r)} imóveis")
            todas.extend(r)
        except Exception as e:
            print(f"[zukerman] erro: {e}")

    if "sold" in args.fontes:
        print(f"[sold] buscando {ufs}...")
        try:
            r = sold.buscar(estados=ufs)
            print(f"[sold] {len(r)} imóveis")
            todas.extend(r)
        except Exception as e:
            print(f"[sold] erro: {e}")

    if "caixa" in args.fontes:
        print(f"[caixa] buscando {ufs} (abre uma janela de navegador, não feche)...")
        try:
            r = caixa.buscar(ufs)
            print(f"[caixa] {len(r)} imóveis")
            todas.extend(r)
        except Exception as e:
            print(f"[caixa] erro: {e}")

    with connect() as conn:
        n = upsert_listings(conn, todas)
    print(f"Total salvo/atualizado no banco: {n}")


def cmd_analisar(args):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM imoveis WHERE status = 'novo'").fetchall()
        print(f"{len(rows)} imóveis novos para analisar")
        analisados = 0
        for row in rows:
            listing = dict(row)
            l = SimpleNamespace(
                valor_lance_atual=listing["valor_lance_atual"],
                valor_avaliacao=listing["valor_avaliacao"],
                ocupado=listing["ocupado"],
            )
            resultado = analise_rapida(l)
            if resultado is None:
                continue
            inputs, r = resultado
            save_analise(conn, listing["id"], inputs.__dict__, r)
            analisados += 1
        print(f"{analisados} imóveis analisados (triagem automática)")


def cmd_listar(args):
    with connect() as conn:
        rows = listar_com_ultima_analise(conn)
    print(f"{len(rows)} imóveis (status != descartado)\n")
    for row in rows[: args.limit]:
        roi = row["roi_anualizado_pct"]
        roi_str = f"{roi:6.1f}%" if roi is not None else "  --  "
        print(f"[{row['fonte']:9s}] {roi_str} a.a. | {row['cidade'] or '?':20s} | "
              f"R$ {row['valor_lance_atual'] or 0:>12,.0f} | {row['status']:10s} | {row['url']}")


def main():
    parser = argparse.ArgumentParser(description="Leilões de Imóveis — busca, análise e dashboard")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_atualizar = sub.add_parser("atualizar", help="Busca imóveis nos sites configurados")
    p_atualizar.add_argument("--fontes", nargs="+", default=["zukerman", "sold"], choices=["zukerman", "sold", "caixa"])
    p_atualizar.add_argument("--ufs", nargs="+", default=["SP"])
    p_atualizar.set_defaults(func=cmd_atualizar)

    p_analisar = sub.add_parser("analisar", help="Roda a triagem automática (premissas padrão) nos imóveis novos")
    p_analisar.set_defaults(func=cmd_analisar)

    p_listar = sub.add_parser("listar", help="Lista os imóveis ordenados por retorno anualizado")
    p_listar.add_argument("--limit", type=int, default=30)
    p_listar.set_defaults(func=cmd_listar)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
