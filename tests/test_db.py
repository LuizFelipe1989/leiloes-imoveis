import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import connect, upsert_listings, Listing, marcar_encerrados_por_ausencia, marcar_encerrados_por_data_passada


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


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_ausencia(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_marcar_encerrados_por_data_passada(Path(d))
    print("OK - todos os testes passaram")
