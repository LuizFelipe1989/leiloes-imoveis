import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculator.arremates import indexar_por_regiao, resumo_regiao


def _arremate(**kwargs):
    base = dict(cidade="São Paulo", estado="SP", grupo="apartamento", valor=300_000,
                area_m2=60, preco_m2=5000, data="2026-09-10 10:00:00", endereco="Rua X")
    base.update(kwargs)
    return base


def test_indexa_e_resume_por_regiao_normalizada():
    # cidade vem acentuada da Sold, mas nosso banco guarda tipo "SAO PAULO" sem acento —
    # o índice tem que casar os dois. `indexar_por_regiao` confia que a lista de entrada
    # já vem ordenada por data desc (é o que `buscar_arremates_recentes` garante) —
    # por isso o mais recente vem primeiro aqui também.
    arremates = [
        _arremate(valor=320_000, preco_m2=5200, data="2026-09-10 10:00:00"),  # mais recente, primeiro
        _arremate(valor=300_000, preco_m2=5000, data="2026-09-01 10:00:00"),
        _arremate(cidade="Rio de Janeiro", estado="RJ"),  # outra cidade, não deve entrar
        _arremate(grupo="casa"),  # outro grupo, não deve entrar
        _arremate(grupo=""),  # sem grupo classificável, descartado no índice
    ]
    indice = indexar_por_regiao(arremates)
    assert len(indice) == 3  # SP/apartamento, SP/casa, RJ/apartamento

    resumo = resumo_regiao(indice, "SP", "SAO PAULO", "Apartamento")
    assert resumo is not None
    assert resumo["n_amostra"] == 2
    assert resumo["ultimo_valor"] == 320_000  # o primeiro da lista (mais recente)


def test_resumo_regiao_sem_amostra_retorna_none():
    indice = indexar_por_regiao([_arremate()])
    assert resumo_regiao(indice, "SP", "Campinas", "Apartamento") is None  # cidade não bateu
    assert resumo_regiao(indice, "SP", "São Paulo", "Terreno") is None  # terreno não é casa/apartamento


if __name__ == "__main__":
    test_indexa_e_resume_por_regiao_normalizada()
    test_resumo_regiao_sem_amostra_retorna_none()
    print("OK - todos os testes passaram")
