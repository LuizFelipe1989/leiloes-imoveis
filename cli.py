"""
CLI do projeto de leilões de imóveis.

Uso:
    python cli.py atualizar --fontes zukerman sold caixa --ufs SP
    python cli.py analisar
    python cli.py dashboard
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from calculator import analise_rapida
from calculator import mercado
from calculator import arremates
from db import (
    connect, save_analise, upsert_listings, listar_com_ultima_analise,
    chave_regiao, get_comparavel_cache, set_comparavel_cache,
    marcar_encerrados_por_ausencia, marcar_encerrados_por_data_passada,
)
from db.store import Listing
from scraper import zukerman, sold, bancodobrasil
from scraper.filters import eh_residencial_ou_terreno

CAIXA_SCRIPT = Path(__file__).parent / "scraper" / "caixa.py"


def _buscar_caixa_isolado(ufs: list[str], tentativas: int = 3) -> list[Listing]:
    """Roda scraper/caixa.py num subprocesso isolado (ver comentário no topo
    daquele arquivo). O Chromium com janela real às vezes fecha sozinho no meio
    da navegação (flakiness observada, não 100% reproduzível/explicada) — vale
    a pena tentar de novo antes de desistir."""
    erro = None
    for tentativa in range(1, tentativas + 1):
        proc = subprocess.run(
            [sys.executable, str(CAIXA_SCRIPT), *ufs],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            try:
                dados = json.loads(proc.stdout)
                return [Listing(**d) for d in dados]
            except json.JSONDecodeError as e:
                erro = f"saída não era JSON válido: {e}"
        else:
            erro = proc.stderr.strip()[-2000:]
        if tentativa < tentativas:
            print(f"[caixa] tentativa {tentativa} falhou ({erro[:200]!r}), tentando de novo...")
    raise RuntimeError(f"subprocesso da Caixa falhou após {tentativas} tentativas: {erro}")


def cmd_atualizar(args):
    ufs = [u.upper() for u in args.ufs]
    todas = []
    ids_vistos_por_fonte: dict[str, set] = {}

    if "zukerman" in args.fontes:
        print(f"[zukerman] buscando {ufs}...")
        vistos_zukerman: set = set()
        try:
            r = zukerman.buscar(ufs)
            print(f"[zukerman] {len(r)} imóveis")
            todas.extend(r)
            vistos_zukerman |= {l.id for l in r}
        except Exception as e:
            print(f"[zukerman] erro: {e}")

        # bancos que usam a Zuk como leiloeiro oficial (Itaú, Bradesco, Santander) —
        # a página de cada banco não filtra por UF (só devolve leva nacional única),
        # então filtramos por estado depois de parsear.
        for banco in zukerman.BANCOS_SLUG:
            try:
                r_banco = [l for l in zukerman.buscar_por_banco(banco) if l.estado in ufs]
                print(f"[zukerman:{banco}] {len(r_banco)} imóveis (em {ufs})")
                todas.extend(r_banco)
                vistos_zukerman |= {l.id for l in r_banco}
            except Exception as e:
                print(f"[zukerman:{banco}] erro: {e}")
        if vistos_zukerman:
            ids_vistos_por_fonte["zukerman"] = vistos_zukerman

    if "sold" in args.fontes:
        print(f"[sold] buscando {ufs}...")
        try:
            r = sold.buscar(estados=ufs)
            print(f"[sold] {len(r)} imóveis")
            todas.extend(r)
            ids_vistos_por_fonte["sold"] = {l.id for l in r}
        except Exception as e:
            print(f"[sold] erro: {e}")

    if "caixa" in args.fontes:
        print(f"[caixa] buscando {ufs} (abre uma janela de navegador, não feche)...")
        try:
            r = _buscar_caixa_isolado(ufs)
            print(f"[caixa] {len(r)} imóveis")
            todas.extend(r)
            ids_vistos_por_fonte["caixa"] = {l.id for l in r}
        except Exception as e:
            print(f"[caixa] erro: {e}")

    if "bancodobrasil" in args.fontes:
        print(f"[bancodobrasil] buscando {ufs}...")
        try:
            r = bancodobrasil.buscar(estados=ufs)
            print(f"[bancodobrasil] {len(r)} imóveis")
            todas.extend(r)
            ids_vistos_por_fonte["bancodobrasil"] = {l.id for l in r}
        except Exception as e:
            print(f"[bancodobrasil] erro: {e}")

    antes = len(todas)
    # foco atual: só residencial e terreno (sem salas/imóveis comerciais)
    todas = [l for l in todas if eh_residencial_ou_terreno(l.tipo_imovel)]
    print(f"Filtrados {antes - len(todas)} imóveis comerciais (foco em residencial/terreno)")

    with connect() as conn:
        n = upsert_listings(conn, todas)
        print(f"Total salvo/atualizado no banco: {n}")

        # imóvel que sumiu da fonte (não veio nessa leva) = leilão/venda não está
        # mais disponível — usa a lista CRUA (ids_vistos_por_fonte), não a `todas`
        # filtrada, senão um imóvel comercial ainda ativo seria marcado como encerrado
        # só por ter sido descartado da nossa seleção.
        encerrados_ausencia = 0
        for fonte, ids_vistos in ids_vistos_por_fonte.items():
            if not ids_vistos:
                # busca bem-sucedida mas devolveu 0 imóveis é suspeito (site pode ter
                # mudado de estrutura) — mais seguro não presumir que tudo encerrou
                print(f"[{fonte}] 0 imóveis vistos, pulando verificação de encerrados (suspeito demais pra confiar)")
                continue
            encerrados_ausencia += marcar_encerrados_por_ausencia(conn, fonte, ids_vistos)

        encerrados_data = marcar_encerrados_por_data_passada(conn)
        print(f"Encerrados: {encerrados_ausencia} por terem sumido da fonte, "
              f"{encerrados_data} por data de leilão já vencida")


def cmd_limpar_comerciais(args):
    """Marca como 'descartado' imóveis já salvos que não são residencial/terreno."""
    with connect() as conn:
        rows = conn.execute("SELECT id, tipo_imovel FROM imoveis WHERE status != 'descartado'").fetchall()
        descartados = 0
        for row in rows:
            if not eh_residencial_ou_terreno(row["tipo_imovel"]):
                conn.execute(
                    "UPDATE imoveis SET status = 'descartado' WHERE id = ?", (row["id"],)
                )
                descartados += 1
    print(f"{descartados} imóveis comerciais marcados como descartados")


def cmd_limpar_encerrados(args):
    """Marca como 'encerrado' imóveis com data de leilão já vencida (Zukerman/Sold).
    Não pega os que sumiram da fonte sem deixar data — isso só acontece rodando
    `atualizar` de novo, que já chama isso automaticamente."""
    with connect() as conn:
        n = marcar_encerrados_por_data_passada(conn)
    print(f"{n} imóveis marcados como encerrados (data de leilão já vencida)")


def _obter_preco_m2_por_regiao(conn, regioes: set, verbose: bool = True) -> dict:
    """Resolve preço/m² de comparáveis (QuintoAndar) pra cada (estado,cidade,bairro,grupo),
    usando cache em disco (comparaveis_cache) pra não bater na API de novo em toda execução.
    `regioes` já vem filtrado pra só casa/apartamento (ver cmd_analisar) — grupo nunca é ''."""
    preco_por_chave = {}
    consultadas = 0
    for estado, cidade, bairro, grupo in regioes:
        chave = chave_regiao(estado, cidade, bairro, grupo)
        cache = get_comparavel_cache(conn, chave)
        if cache is None:
            try:
                # passa `grupo` como tipo_imovel: estimar_preco_m2_regiao só usa isso
                # pra reclassificar em grupo_tipo(), que já é idempotente aqui
                resultado = mercado.estimar_preco_m2_regiao(cidade, estado, bairro, grupo)
            except Exception:
                resultado = None
            set_comparavel_cache(
                conn, chave, resultado is not None,
                resultado["preco_m2_mediano"] if resultado else None,
                resultado["n_comparaveis"] if resultado else None,
            )
            consultadas += 1
            cache = {"encontrado": int(resultado is not None),
                     "preco_m2_mediano": resultado["preco_m2_mediano"] if resultado else None}
        preco_por_chave[chave] = cache["preco_m2_mediano"] if cache["encontrado"] else None
    if verbose:
        cobertas = sum(1 for v in preco_por_chave.values() if v)
        print(f"Comparáveis de mercado: {len(regioes)} região(ões) casa/apartamento, {consultadas} nova(s) "
              f"consulta(s) ao QuintoAndar, {cobertas} região(ões) com cobertura (cache válido por 30 dias)")
    return preco_por_chave


def cmd_analisar(args):
    with connect() as conn:
        status_alvo = ("novo", "analisado") if args.recalcular_tudo else ("novo",)
        placeholders = ",".join("?" for _ in status_alvo)
        rows = conn.execute(f"SELECT * FROM imoveis WHERE status IN ({placeholders})", status_alvo).fetchall()
        print(f"{len(rows)} imóveis para analisar")

        # só casa/apartamento entram na busca de comparáveis (QuintoAndar não lista
        # terreno, e não vale a pena bater na API pros que nem vão poder usar o resultado)
        regioes = set()
        for r in rows:
            if not r["cidade"]:
                continue
            grupo = mercado.grupo_tipo(r["tipo_imovel"])
            if grupo:
                regioes.add((r["estado"], r["cidade"], r["bairro"], grupo))
        preco_m2_por_regiao = _obter_preco_m2_por_regiao(conn, regioes)

        # arremates reais (leilões concluídos com lance vencedor) — busca nacional
        # única por execução, indexada em memória; é só complemento informativo,
        # não entra na conta do valor de venda (amostra pequena demais pra isso)
        print("Buscando arremates recentes na Superbid (complemento informativo)...")
        try:
            indice_arremates = arremates.indexar_por_regiao(arremates.buscar_arremates_recentes())
            print(f"{sum(len(v) for v in indice_arremates.values())} arremates indexados em "
                  f"{len(indice_arremates)} combinação(ões) de região/tipo")
        except Exception as e:
            print(f"[arremates] erro ao buscar (seguindo sem esse complemento): {e}")
            indice_arremates = {}

        analisados = 0
        com_comparaveis = 0
        com_arremate = 0
        for row in rows:
            listing = dict(row)
            l = SimpleNamespace(
                valor_lance_atual=listing["valor_lance_atual"],
                valor_avaliacao=listing["valor_avaliacao"],
                ocupado=listing["ocupado"],
                area_m2=listing["area_m2"],
                tipo_imovel=listing["tipo_imovel"],
            )
            grupo = mercado.grupo_tipo(listing["tipo_imovel"])
            chave = chave_regiao(listing["estado"], listing["cidade"], listing["bairro"], grupo) if grupo else None
            arremate_info = arremates.resumo_regiao(
                indice_arremates, listing["estado"], listing["cidade"], listing["tipo_imovel"]
            )
            resultado = analise_rapida(
                l, preco_m2_mercado=preco_m2_por_regiao.get(chave) if chave else None,
                arremate_info=arremate_info,
            )
            if resultado is None:
                continue
            inputs, r = resultado
            if inputs.fonte_venda_estimada == "comparaveis_quintoandar":
                com_comparaveis += 1
            if arremate_info:
                com_arremate += 1
            save_analise(conn, listing["id"], inputs.__dict__, r)
            analisados += 1
        print(f"{analisados} imóveis analisados — {com_comparaveis} com valor de venda baseado em "
              f"comparáveis reais de mercado, {analisados - com_comparaveis} pelo haircut genérico sobre a avaliação "
              f"({com_arremate} também com arremate recente na região como complemento)")


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
    p_atualizar.add_argument("--fontes", nargs="+", default=["zukerman", "sold"],
                              choices=["zukerman", "sold", "caixa", "bancodobrasil"])
    p_atualizar.add_argument("--ufs", nargs="+", default=["SP", "MG"])
    p_atualizar.set_defaults(func=cmd_atualizar)

    p_analisar = sub.add_parser("analisar", help="Roda a triagem automática (premissas padrão) nos imóveis novos")
    p_analisar.add_argument("--recalcular-tudo", action="store_true",
                             help="Reanalisa também os imóveis já analisados (não só os novos) — útil depois de mudar a lógica de análise")
    p_analisar.set_defaults(func=cmd_analisar)

    p_limpar = sub.add_parser("limpar-comerciais", help="Descarta imóveis comerciais já salvos (foco em residencial/terreno)")
    p_limpar.set_defaults(func=cmd_limpar_comerciais)

    p_limpar_enc = sub.add_parser("limpar-encerrados", help="Marca como encerrados imóveis com data de leilão já vencida")
    p_limpar_enc.set_defaults(func=cmd_limpar_encerrados)

    p_listar = sub.add_parser("listar", help="Lista os imóveis ordenados por retorno anualizado")
    p_listar.add_argument("--limit", type=int, default=30)
    p_listar.set_defaults(func=cmd_listar)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
