-- Schema do banco de oportunidades de leilão de imóveis.

CREATE TABLE IF NOT EXISTS imoveis (
    id                  TEXT PRIMARY KEY,   -- "<fonte>:<id_no_site>"
    fonte               TEXT NOT NULL,      -- caixa | zukerman | sold
    titulo              TEXT,
    endereco            TEXT,
    bairro              TEXT,
    cidade              TEXT,
    estado              TEXT,
    tipo_imovel         TEXT,
    area_m2             REAL,
    valor_avaliacao     REAL,
    valor_lance_atual   REAL,               -- lance mínimo vigente (1ª ou 2ª praça, o que estiver aberto)
    praca               TEXT,               -- "1ª praça" | "2ª praça" | "praça única"
    data_leilao         TEXT,
    ocupado             TEXT,               -- "sim" | "nao" | "desconhecido"
    url                 TEXT,
    imagem_url          TEXT,
    edital_url          TEXT,
    status              TEXT NOT NULL DEFAULT 'novo',  -- novo | analisado | descartado | arrematado | vendido
    primeira_vez_visto  TEXT NOT NULL,
    ultima_atualizacao  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_imoveis_fonte ON imoveis(fonte);
CREATE INDEX IF NOT EXISTS idx_imoveis_cidade ON imoveis(cidade);
CREATE INDEX IF NOT EXISTS idx_imoveis_status ON imoveis(status);

CREATE TABLE IF NOT EXISTS analises (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    imovel_id       TEXT NOT NULL REFERENCES imoveis(id),
    inputs_json     TEXT NOT NULL,
    desconto_pct    REAL,
    investimento_total REAL,
    lucro_liquido   REAL,
    roi_pct         REAL,
    roi_anualizado_pct REAL,
    criado_em       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analises_imovel ON analises(imovel_id);
