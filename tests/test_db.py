import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import (
    connect, upsert_listings, Listing, marcar_encerrados_por_ausencia,
    marcar_encerrados_por_data_passada, sincronizar_modalidade,
)


def test_marcar_encerrados_por_ausencia(tmp_path):
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [
            Listing(fonte="zukerman", id_no_site="1", titulo="A"),
            Listing(fonte="zukerman", id_no_site="2", titulo="B"),
            Listing(fonte="sold", id_no_site="9", titulo="Outra fonte, não deve ser afetada"),
        ])
        # nessa nova leva só o "1" ainda existe -> "2" deve ser marcado como encerrado
        n = marcar_encerrados_por_ausencia(conn, "zukerman", {"zukerman:1"})
        assert n == 1
        row1 = conn.execute("SELECT status FROM imoveis WHERE id='zukerman:1'").fetchone()
        row2 = conn.execute("SELECT status FROM imoveis WHERE id='zukerman:2'").fetchone()
        row9 = conn.execute("SELECT status FROM imoveis WHERE id='sold:9'").fetchone()
        assert row1["status"] == "novo"
        assert row2["status"] == "encerrado"
        assert row9["status"] == "novo"  # outra fonte não sofre o "sumiço"


def test_marcar_encerrados_por_data_passada(tmp_path):
    db_path = tmp_path / "test2.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [
            Listing(fonte="sold", id_no_site="1", data_leilao="2000-01-01 10:00:00"),  # passado
            Listing(fonte="zukerman", id_no_site="2", data_leilao="01/01/2000 às 10:00"),  # passado, outro formato
            Listing(fonte="sold", id_no_site="3", data_leilao="2999-01-01 10:00:00"),  # futuro
            Listing(fonte="caixa", id_no_site="4", data_leilao=""),  # sem data, não deve ser tocado
        ])
        n = marcar_encerrados_por_data_passada(conn)
        assert n == 2
        status = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM imoveis")}
        assert status["sold:1"] == "encerrado"
        assert status["zukerman:2"] == "encerrado"
        assert status["sold:3"] == "novo"
        assert status["caixa:4"] == "novo"


def test_rescrape_da_caixa_nao_desfaz_modalidade_sincronizada(tmp_path):
    db_path = tmp_path / "test3.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [
            Listing(fonte="caixa", id_no_site="1", valor_avaliacao=100_000, valor_lance_atual=60_000),
            Listing(fonte="leilaoimovel", id_no_site="2", modalidade="venda_direta"),
        ])
        assert sincronizar_modalidade(conn, "caixa:1", "venda_online") is True
        # novo re-scrape da Caixa: o Listing vem com o default "leilao"
        upsert_listings(conn, [
            Listing(fonte="caixa", id_no_site="1", valor_avaliacao=100_000, valor_lance_atual=55_000),
            Listing(fonte="leilaoimovel", id_no_site="2", modalidade="venda_direta"),
        ])
        row = conn.execute("SELECT modalidade, valor_lance_atual FROM imoveis WHERE id='caixa:1'").fetchone()
        assert row["modalidade"] == "venda_online"  # não foi sobrescrita
        assert row["valor_lance_atual"] == 55_000   # o resto continua sendo atualizado
        # outras fontes seguem mandando na própria modalidade
        row2 = conn.execute("SELECT modalidade FROM imoveis WHERE id='leilaoimovel:2'").fetchone()
        assert row2["modalidade"] == "venda_direta"


def test_sincronizar_modalidade_apaga_analise_antiga(tmp_path):
    db_path = tmp_path / "test4.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1")])
        conn.execute("UPDATE imoveis SET status='analisado' WHERE id='caixa:1'")
        conn.execute(
            "INSERT INTO analises (imovel_id, inputs_json, criado_em) VALUES ('caixa:1', '{}', '2026-01-01')"
        )
        assert sincronizar_modalidade(conn, "caixa:1", "venda_direta") is True
        assert conn.execute("SELECT COUNT(*) FROM analises WHERE imovel_id='caixa:1'").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM imoveis WHERE id='caixa:1'").fetchone()["status"] == "novo"
        # já está na modalidade pedida: não faz nada
        assert sincronizar_modalidade(conn, "caixa:1", "venda_direta") is False


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_ausencia(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_data_passada(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_rescrape_da_caixa_nao_desfaz_modalidade_sincronizada(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_sincronizar_modalidade_apaga_analise_antiga(Path(d))
    print("OK - todos os testes passaram")
