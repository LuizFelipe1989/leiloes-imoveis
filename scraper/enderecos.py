"""
Extração de bairro a partir de string de endereço em texto livre.

Cada leiloeiro formata o endereço de um jeito diferente — às vezes separado
por vírgula ("Rua X, 100, Bairro, Cidade/UF, CEP"), às vezes por hífen
("Rua X, 100 - Bairro - Cidade/UF, CEP"), às vezes os dois no mesmo texto.
Em vez de um parser por formato, isso tenta os dois separadores e fica com o
que sobrar de mais específico depois de descartar os pedaços que claramente
não são bairro (CEP, UF isolada, "cidade/uf" colado, ou a própria cidade).
"""

import re


def _eh_ruido(pedaco: str, cidade: str) -> bool:
    p = pedaco.strip()
    if not p:
        return True
    if re.search(r"\bcep\b", p, re.IGNORECASE) or re.fullmatch(r"\d{5}-?\d{3}\.?", p):
        return True
    if re.fullmatch(r"[A-Z]{2}", p):
        return True
    if "/" in p:
        return True
    if cidade and cidade.strip().lower() == p.lower():
        return True
    return False


def _tentar_virgula(endereco_completo: str, cidade: str) -> tuple[str, str]:
    # formato fino: "rua, número, bairro, cidade, uf, cep" — o bairro sobra como
    # o último pedaço depois de descartar cidade/uf/cep do final.
    partes = [p.strip() for p in endereco_completo.split(",") if p.strip()]
    while partes and _eh_ruido(partes[-1], cidade):
        partes.pop()
    if len(partes) >= 3:
        return ", ".join(partes[:-1]), partes[-1]
    if partes:
        return ", ".join(partes), ""
    return endereco_completo, ""


def _tentar_hifen(endereco_completo: str, cidade: str) -> tuple[str, str]:
    # formato grosso: "rua e número - bairro - cidade/uf, cep" — só 3 blocos,
    # o bairro é sempre o do meio (não dá pra descartar o último e recontar).
    partes = [p.strip() for p in endereco_completo.split(" - ") if p.strip()]
    if len(partes) >= 3 and _eh_ruido(partes[-1], cidade):
        return partes[0], partes[1]
    return endereco_completo, ""


def extrair_bairro(endereco_completo: str, cidade: str = "") -> tuple[str, str]:
    """Retorna (endereco_sem_bairro, bairro). Tenta vírgula e hífen, fica com o melhor resultado."""
    if not endereco_completo:
        return "", ""
    candidatos = [
        _tentar_virgula(endereco_completo, cidade),
        _tentar_hifen(endereco_completo, cidade),
    ]
    # prefere o candidato que efetivamente achou um bairro; entre os dois, o de endereço mais curto
    com_bairro = [c for c in candidatos if c[1]]
    if com_bairro:
        return min(com_bairro, key=lambda c: len(c[0]))
    return candidatos[0]
