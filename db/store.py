"""Camada de acesso ao banco SQLite de oportunidades de leilão."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "leiloes.db"


@dataclass
class Listing:
    fonte: str
    id_no_site: str
    titulo: str = ""
    endereco: str = ""
    bairro: str = ""
    cidade: str = ""
    estado: str = ""
    tipo_imovel: str = ""
    banco: str = ""
    area_m2: Optional[float] = None
    valor_avaliacao: Optional[float] = None
    valor_lance_atual: Optional[float] = None
    praca: str = ""
    data_leilao: str = ""
    ocupado: str = "desconhecido"
    url: str = ""
    imagem_url: str = ""
    edital_url: str = ""

    @property
    def id(self) -> str:
        return f"{self.fonte}:{self.id_no_site}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # migração leve pra bases criadas antes da coluna existir
        colunas = {row[1] for row in conn.execute("PRAGMA table_info(imoveis)")}
        if "banco" not in colunas:
            conn.execute("ALTER TABLE imoveis ADD COLUMN banco TEXT")


@contextmanager
def connect(db_path: Path = DEFAULT_DB_PATH):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> None:
    now = _now()
    row = conn.execute("SELECT id FROM imoveis WHERE id = ?", (listing.id,)).fetchone()
    data = asdict(listing)
    data.pop("id_no_site", None)
    data["id"] = listing.id
    if row is None:
        data["primeira_vez_visto"] = now
        data["ultima_atualizacao"] = now
        data["status"] = "novo"
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO imoveis ({cols}) VALUES ({placeholders})", tuple(data.values()))
    else:
        data["ultima_atualizacao"] = now
        set_clause = ", ".join(f"{k} = ?" for k in data if k != "id")
        values = [v for k, v in data.items() if k != "id"] + [listing.id]
        conn.execute(f"UPDATE imoveis SET {set_clause} WHERE id = ?", values)


def upsert_listings(conn: sqlite3.Connection, listings: Iterable[Listing]) -> int:
    count = 0
    for listing in listings:
        upsert_listing(conn, listing)
        count += 1
    return count


def save_analise(conn: sqlite3.Connection, imovel_id: str, inputs: dict, resultado) -> None:
    conn.execute(
        """INSERT INTO analises
           (imovel_id, inputs_json, desconto_pct, investimento_total, lucro_liquido, roi_pct, roi_anualizado_pct, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            imovel_id,
            json.dumps(inputs, ensure_ascii=False),
            resultado.desconto_pct,
            resultado.investimento_total,
            resultado.lucro_liquido,
            resultado.roi_pct,
            resultado.roi_anualizado_pct,
            _now(),
        ),
    )
    conn.execute("UPDATE imoveis SET status = 'analisado', ultima_atualizacao = ? WHERE id = ? AND status = 'novo'",
                 (_now(), imovel_id))


STATUS_VALIDOS = ("novo", "analisado", "descartado", "arrematado", "vendido", "encerrado")


def set_status(conn: sqlite3.Connection, imovel_id: str, status: str) -> None:
    assert status in STATUS_VALIDOS
    conn.execute("UPDATE imoveis SET status = ?, ultima_atualizacao = ? WHERE id = ?", (status, _now(), imovel_id))


def marcar_encerrados_por_ausencia(conn: sqlite3.Connection, fonte: str, ids_vistos: set[str]) -> int:
    """Marca como 'encerrado' imóveis de `fonte` que sumiram da fonte (não vieram
    na leva mais recente raspada) — o leilão/venda já não está mais disponível no
    site de origem, seja porque foi arrematado/vendido ou porque o edital venceu."""
    rows = conn.execute(
        "SELECT id FROM imoveis WHERE fonte = ? AND status NOT IN ('descartado', 'encerrado')", (fonte,)
    ).fetchall()
    ausentes = [r["id"] for r in rows if r["id"] not in ids_vistos]
    if not ausentes:
        return 0
    now = _now()
    conn.executemany(
        "UPDATE imoveis SET status = 'encerrado', ultima_atualizacao = ? WHERE id = ?",
        [(now, imovel_id) for imovel_id in ausentes],
    )
    return len(ausentes)


def _parse_data_leilao(texto: str) -> Optional[datetime]:
    texto = (texto or "").strip()
    if not texto:
        return None
    formatos = ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y às %H:%M", "%d/%m/%Y %H:%M"]
    for fmt in formatos:
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def marcar_encerrados_por_data_passada(conn: sqlite3.Connection) -> int:
    """Marca como 'encerrado' imóveis cuja data de leilão já passou (Zukerman/Sold
    trazem data; Caixa/Banco do Brasil não, então não são afetados por isso)."""
    rows = conn.execute(
        "SELECT id, data_leilao FROM imoveis WHERE status NOT IN ('descartado', 'encerrado') AND data_leilao != ''"
    ).fetchall()
    agora = datetime.now()
    vencidos = []
    for row in rows:
        data = _parse_data_leilao(row["data_leilao"])
        if data and data < agora:
            vencidos.append(row["id"])
    if not vencidos:
        return 0
    now = _now()
    conn.executemany(
        "UPDATE imoveis SET status = 'encerrado', ultima_atualizacao = ? WHERE id = ?",
        [(now, imovel_id) for imovel_id in vencidos],
    )
    return len(vencidos)


def chave_regiao(estado: str, cidade: str, bairro: str, grupo: str = "") -> str:
    norm = lambda s: (s or "").strip().lower()
    return f"{norm(estado)}|{norm(cidade)}|{norm(bairro)}|{norm(grupo)}"


def get_comparavel_cache(conn: sqlite3.Connection, chave: str, max_idade_dias: int = 30) -> Optional[dict]:
    row = conn.execute("SELECT * FROM comparaveis_cache WHERE chave = ?", (chave,)).fetchone()
    if row is None:
        return None
    idade = datetime.now(timezone.utc) - datetime.fromisoformat(row["atualizado_em"])
    if idade.days > max_idade_dias:
        return None
    return dict(row)


def set_comparavel_cache(conn: sqlite3.Connection, chave: str, encontrado: bool,
                          preco_m2_mediano: Optional[float], n_comparaveis: Optional[int]) -> None:
    conn.execute(
        """INSERT INTO comparaveis_cache (chave, encontrado, preco_m2_mediano, n_comparaveis, atualizado_em)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(chave) DO UPDATE SET
             encontrado = excluded.encontrado,
             preco_m2_mediano = excluded.preco_m2_mediano,
             n_comparaveis = excluded.n_comparaveis,
             atualizado_em = excluded.atualizado_em""",
        (chave, int(encontrado), preco_m2_mediano, n_comparaveis, _now()),
    )


def listar_com_ultima_analise(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT i.*,
               a.desconto_pct, a.investimento_total, a.lucro_liquido, a.roi_pct, a.roi_anualizado_pct,
               a.inputs_json, a.criado_em AS analise_criado_em
        FROM imoveis i
        LEFT JOIN (
            SELECT a1.*
            FROM analises a1
            INNER JOIN (
                SELECT imovel_id, MAX(criado_em) AS max_criado_em
                FROM analises GROUP BY imovel_id
            ) latest ON a1.imovel_id = latest.imovel_id AND a1.criado_em = latest.max_criado_em
        ) a ON a.imovel_id = i.id
        WHERE i.status NOT IN ('descartado', 'encerrado')
        ORDER BY (a.roi_anualizado_pct IS NULL), a.roi_anualizado_pct DESC
        """
    ).fetchall()
