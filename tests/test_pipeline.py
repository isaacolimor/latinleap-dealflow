"""Pruebas de la lógica determinista (no requieren red ni API key).

    python -m pytest -q
"""
from datetime import datetime, timedelta, timezone

import config
from dealflow import scoring
from run import prefiltro


def _s(**k):
    base = dict(nombre="Ejemplo", pais="México", es_startup=True, es_tecnologia=True, etapa="seed",
                sector="fintech", ai_first=1, copycat=1, traccion=1, momento=1, purpose=1)
    base.update(k)
    return base


def test_pesos_suman_100():
    assert sum(config.PESOS.values()) == 100


def test_score_maximo_es_100():
    score, desglose = scoring.calcular_score(_s())
    assert score == 100
    assert desglose["geografia"] == 15


def test_geografia_por_niveles():
    assert scoring.calcular_score(_s(pais="Argentina"))[1]["geografia"] == 10
    assert scoring.calcular_score(_s(pais="Panama"))[1]["geografia"] == 5   # tolera falta de acento
    assert scoring.clasificar(_s(pais="Brasil"))["estado"] == "descartada"


def test_portafolio_no_se_recomienda():
    c = scoring.clasificar(_s(nombre="Dapta.ai"))
    assert c["estado"] == "portafolio" and c["score"] == 0 and c["score_bruto"] == 100


def test_etapa_tardia_se_descarta():
    assert "etapa_tardia" in scoring.clasificar(_s(etapa="serie_a_o_mas"))["filtros_fallidos"]


def test_umbral():
    # sector 20 + geo 15 + momento 5 + purpose 5 = 45 -> bajo umbral
    c = scoring.clasificar(_s(ai_first=0, copycat=0, traccion=0))
    assert c["score"] == 45 and c["estado"] == "bajo_umbral"


def test_normalizar_deduplica_variantes():
    assert scoring.normalizar("Dapta.ai") == scoring.normalizar("DAPTA") == "dapta"
    assert scoring.normalizar("Fintech Ñandú S.A.S.") == scoring.normalizar("fintech nandu")


def test_prefiltro():
    hoy = datetime.now(timezone.utc)
    lim = hoy - timedelta(days=21)
    a = {"url": "u1", "fecha": hoy.isoformat(), "titulo": "Alice raises $40M Series D", "tags": []}
    assert prefiltro(a, set(), lim) == "etapa_tardia"
    a = {"url": "u2", "fecha": hoy.isoformat(), "titulo": "Imersa raises $50K", "tags": ["Brazil"]}
    assert prefiltro(a, set(), lim) == "solo_brasil"
    a = {"url": "u3", "fecha": hoy.isoformat(), "titulo": "Startup chilena levanta pre-seed", "tags": []}
    assert prefiltro(a, set(), lim) is None
    assert prefiltro(a, {"u3"}, lim) == "ya_visto"
    a = {"url": "u4", "fecha": (hoy - timedelta(days=40)).isoformat(), "titulo": "x", "tags": []}
    assert prefiltro(a, set(), lim) == "fuera_de_ventana"
