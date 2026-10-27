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


def test_modalidade_vazia_da_fonte_nao_mexe_na_que_ja_temos(tmp_path):
    # rótulo desconhecido no CSV da Caixa -> caixa.py manda modalidade="" e o
    # upsert mantém a classificação anterior (em vez de chutar "leilao")
    db_path = tmp_path / "test3.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1", modalidade="venda_online")])
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1", modalidade="", valor_lance_atual=55_000)])
        row = conn.execute("SELECT modalidade, valor_lance_atual FROM imoveis WHERE id='caixa:1'").fetchone()
        assert row["modalidade"] == "venda_online"
        assert row["valor_lance_atual"] == 55_000  # o resto continua sendo atualizado
        # imóvel NOVO com modalidade vazia nasce como leilão (default)
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="2", modalidade="")])
        assert conn.execute("SELECT modalidade FROM imoveis WHERE id='caixa:2'").fetchone()["modalidade"] == "leilao"


def test_upsert_com_modalidade_diferente_reclassifica_e_apaga_analise(tmp_path):
    # a Caixa promove imóveis de "Venda Online" pra "Venda Direta Online" com o tempo
    db_path = tmp_path / "test3b.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1", modalidade="venda_online")])
        conn.execute("UPDATE imoveis SET status='analisado' WHERE id='caixa:1'")
        conn.execute("INSERT INTO analises (imovel_id, inputs_json, criado_em) VALUES ('caixa:1', '{}', '2026-01-01')")
        # mesma modalidade no re-scrape: nada muda, a análise fica
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1", modalidade="venda_online")])
        assert conn.execute("SELECT COUNT(*) FROM analises").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM imoveis WHERE id='caixa:1'").fetchone()["status"] == "analisado"
        # modalidade mudou: reclassifica, apaga a análise (modelo errado) e volta pra 'novo'
        upsert_listings(conn, [Listing(fonte="caixa", id_no_site="1", modalidade="venda_direta")])
        row = conn.execute("SELECT modalidade, status FROM imoveis WHERE id='caixa:1'").fetchone()
        assert (row["modalidade"], row["status"]) == ("venda_direta", "novo")
        assert conn.execute("SELECT COUNT(*) FROM analises").fetchone()[0] == 0
        # outras fontes seguem o mesmo caminho (ex: leilaoimovel marcando venda direta)
        upsert_listings(conn, [Listing(fonte="leilaoimovel", id_no_site="9", modalidade="venda_direta")])
        assert conn.execute("SELECT modalidade FROM imoveis WHERE id='leilaoimovel:9'").fetchone()["modalidade"] == "venda_direta"


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


def test_imovel_encerrado_reabre_se_reaparece_na_coleta(tmp_path):
    db_path = tmp_path / "test5.db"
    with connect(db_path) as conn:
        upsert_listings(conn, [
            Listing(fonte="caixa", id_no_site="1"),   # nunca analisado
            Listing(fonte="caixa", id_no_site="2"),   # já tinha análise
            Listing(fonte="caixa", id_no_site="3"),   # descartado: não deve reabrir
        ])
        conn.execute(
            "INSERT INTO analises (imovel_id, inputs_json, criado_em) VALUES ('caixa:2', '{}', '2026-01-01')"
        )
        conn.execute("UPDATE imoveis SET status='encerrado' WHERE id IN ('caixa:1','caixa:2')")
        conn.execute("UPDATE imoveis SET status='descartado' WHERE id='caixa:3'")
        upsert_listings(conn, [
            Listing(fonte="caixa", id_no_site="1"),
            Listing(fonte="caixa", id_no_site="2"),
            Listing(fonte="caixa", id_no_site="3"),
        ])
        status = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM imoveis")}
        assert status["caixa:1"] == "novo"
        assert status["caixa:2"] == "analisado"
        assert status["caixa:3"] == "descartado"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_ausencia(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_data_passada(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_modalidade_vazia_da_fonte_nao_mexe_na_que_ja_temos(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_sincronizar_modalidade_apaga_analise_antiga(Path(d))
    print("OK - todos os testes passaram")
