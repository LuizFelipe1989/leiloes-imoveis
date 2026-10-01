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
    modalidade: str = "leilao"  # leilao | venda_direta
    area_m2: Optional[float] = None
    valor_avaliacao: Optional[float] = None
    valor_lance_atual: Optional[float] = None
    praca: str = ""
    data_leilao: str = ""
    ocupado: str = "desconhecido"
    url: str = ""
    imagem_url: str = ""
    edital_url: str = ""
    # enriquecimento via smartleiloescaixa.com.br — ver scraper/smartleiloes.py
    quartos: Optional[int] = None
    garagem: Optional[int] = None
    aceita_fgts: Optional[bool] = None
    aceita_consorcio: Optional[bool] = None
    aceita_financiamento: Optional[bool] = None
    aceita_parcelamento: Optional[bool] = None
    tem_acao_judicial: Optional[bool] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

    @property
    def id(self) -> str:
        return f"{self.fonte}:{self.id_no_site}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        # migração leve pra bases criadas antes das colunas existirem
        colunas = {row[1] for row in conn.execute("PRAGMA table_info(imoveis)")}
        if "banco" not in colunas:
            conn.execute("ALTER TABLE imoveis ADD COLUMN banco TEXT")
        if "modalidade" not in colunas:
            conn.execute("ALTER TABLE imoveis ADD COLUMN modalidade TEXT NOT NULL DEFAULT 'leilao'")
        for coluna, tipo in [
            ("quartos", "INTEGER"), ("garagem", "INTEGER"), ("aceita_fgts", "INTEGER"),
            ("aceita_consorcio", "INTEGER"), ("aceita_financiamento", "INTEGER"),
            ("aceita_parcelamento", "INTEGER"), ("tem_acao_judicial", "INTEGER"),
            ("lat", "REAL"), ("lng", "REAL"),
        ]:
            if coluna not in colunas:
                conn.execute(f"ALTER TABLE imoveis ADD COLUMN {coluna} {tipo}")
        # scraper/bancodobrasil.py virou scraper/leilaoimovel.py (agrega vários
        # bancos, não só o BB) — migra o fonte/id de quem já tinha sido salvo
        # com o nome antigo, senão essas linhas voltariam como "novas" duplicadas.
        prefixo_antigo = "bancodobrasil:"
        conn.execute(
            "UPDATE analises SET imovel_id = 'leilaoimovel:' || substr(imovel_id, ?) "
            "WHERE imovel_id LIKE 'bancodobrasil:%'",
            (len(prefixo_antigo) + 1,),
        )
        conn.execute(
            "UPDATE imoveis SET id = 'leilaoimovel:' || substr(id, ?), fonte = 'leilaoimovel', "
            "banco = CASE WHEN banco IS NULL OR banco = '' THEN 'Banco do Brasil' ELSE banco END "
            "WHERE fonte = 'bancodobrasil'",
            (len(prefixo_antigo) + 1,),
        )
        # scraper/caixa.py só passou a marcar banco="Caixa" depois — sem isso o
        # filtro "Banco" do dashboard não tinha como separar Caixa de Itaú/
        # Bradesco/Santander/BB no perfil Venda Direta (ficava sem opção pra
        # filtrar por Caixa, já que o campo vinha vazio).
        conn.execute("UPDATE imoveis SET banco = 'Caixa' WHERE fonte = 'caixa' AND (banco IS NULL OR banco = '')")


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


# Campos preenchidos só pelo enriquecimento do Smart Leilões (ver
# scraper/smartleiloes.py e enriquecer_imovel abaixo) — de propósito FORA do
# UPDATE do upsert_listing comum: as fontes normais (caixa.py, zukerman.py...)
# nem sabem desses campos, então um re-scrape rotineiro sempre os traria como
# None e apagaria o enriquecimento já feito se entrassem no UPDATE geral.
CAMPOS_ENRIQUECIMENTO = (
    "quartos", "garagem", "aceita_fgts", "aceita_consorcio",
    "aceita_financiamento", "aceita_parcelamento", "tem_acao_judicial", "lat", "lng",
)


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
        campos_update = {k: v for k, v in data.items() if k != "id" and k not in CAMPOS_ENRIQUECIMENTO}
        set_clause = ", ".join(f"{k} = ?" for k in campos_update)
        values = list(campos_update.values()) + [listing.id]
        conn.execute(f"UPDATE imoveis SET {set_clause} WHERE id = ?", values)


def upsert_listings(conn: sqlite3.Connection, listings: Iterable[Listing]) -> int:
    count = 0
    for listing in listings:
        upsert_listing(conn, listing)
        count += 1
    return count


def enriquecer_imovel(conn: sqlite3.Connection, imovel_id: str, campos: dict) -> bool:
    """Atualiza só os campos de enriquecimento (CAMPOS_ENRIQUECIMENTO) de um
    imóvel JÁ EXISTENTE, sem tocar no resto (que vem da fonte oficial). Não
    insere linha nova — quem chama decide isso separadamente. Retorna True se
    o imóvel existia e foi atualizado."""
    campos = {k: v for k, v in campos.items() if k in CAMPOS_ENRIQUECIMENTO and v is not None}
    if not campos:
        return conn.execute("SELECT 1 FROM imoveis WHERE id = ?", (imovel_id,)).fetchone() is not None
    set_clause = ", ".join(f"{k} = ?" for k in campos)
    cur = conn.execute(f"UPDATE imoveis SET {set_clause} WHERE id = ?", list(campos.values()) + [imovel_id])
    return cur.rowcount > 0


def promover_para_venda_direta(conn: sqlite3.Connection, imovel_id: str) -> bool:
    """Promove um imóvel de 'leilao' pra 'venda_direta' (nunca o contrário —
    ver scraper/smartleiloes.py, que é quem sabe a modalidade de venda real
    da Caixa). Se o imóvel já tinha sido analisado com o modelo de leilão
    (com comissão de leiloeiro, avaliação do banco como referência etc.),
    apaga essa análise (não faz sentido pra venda direta) e volta o imóvel
    pra 'novo' pra ser recalculado com o modelo certo na próxima
    `cli.py analisar` — sem isso, `listar_com_ultima_analise` continuaria
    juntando a análise antiga (do modelo errado) até uma nova ficar pronta,
    já que ela sempre pega a mais recente independente do status do imóvel.
    Retorna True se promoveu."""
    row = conn.execute("SELECT modalidade, status FROM imoveis WHERE id = ?", (imovel_id,)).fetchone()
    if row is None or row["modalidade"] == "venda_direta":
        return False
    novo_status = "novo" if row["status"] == "analisado" else row["status"]
    conn.execute(
        "UPDATE imoveis SET modalidade = 'venda_direta', status = ? WHERE id = ?",
        (novo_status, imovel_id),
    )
    conn.execute("DELETE FROM analises WHERE imovel_id = ?", (imovel_id,))
    return True


def backfill_se_vazio(conn: sqlite3.Connection, imovel_id: str, campo: str, valor) -> None:
    """Preenche `campo` só se ele estiver vazio (NULL/'''/0) no imóvel já
    existente — nunca sobrescreve um valor que a fonte oficial já forneceu."""
    if valor in (None, ""):
        return
    conn.execute(
        f"UPDATE imoveis SET {campo} = ? WHERE id = ? AND ({campo} IS NULL OR {campo} = '' OR {campo} = 0)",
        (valor, imovel_id),
    )


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
