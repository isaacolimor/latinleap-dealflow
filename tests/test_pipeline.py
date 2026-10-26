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


def test_gemini_parsea_tool_call(monkeypatch):
    """Simula Gemini (formato OpenAI) y verifica petición, descubrimiento de modelo y parseo."""
    import json as _json

    import requests

    from dealflow import extract

    enviado = {}

    class _R:
        def __init__(self, data):
            self.status_code, self.headers, self._d, self.text = 200, {}, data, ""

        def json(self):
            return self._d

    def _get(url, headers, timeout):
        return _R({"data": [{"id": "models/gemini-2.5-flash"}, {"id": "models/gemini-3.8-flash"},
                            {"id": "models/gemini-3.8-flash-lite"}, {"id": "models/gemini-3.8-flash-preview-tts"}]})

    def _post(url, json, timeout, headers):
        enviado.update(url=url, body=json, auth=headers["Authorization"])
        args = {"startups": [{"nombre": "Fronte", "pais": "Colombia"}]}
        return _R({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "registrar_startups", "arguments": _json.dumps(args)}}]}}]})

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEALFLOW_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(config, "PAUSA_GEMINI_SEG", 0)
    monkeypatch.setattr(extract, "_MODELO_GEMINI", None)
    monkeypatch.setattr(extract, "_CANDIDATOS_GEMINI", None)
    art = {"fuente": "X", "fecha": "2026-10-20T00:00:00", "titulo": "T", "tags": [], "texto": "..."}
    assert extract.proveedor() == "gemini"
    assert extract.evaluar(art) == [{"nombre": "Fronte", "pais": "Colombia"}]
    assert enviado["body"]["model"] == "gemini-3.8-flash" and enviado["auth"] == "Bearer k"
    assert enviado["url"].endswith("/openai/chat/completions")


def test_sin_llave_explica_que_falta(monkeypatch):
    import pytest

    from dealflow import extract

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        extract.proveedor()


def test_gemini_salta_modelo_sin_cuota(monkeypatch):
    """Si un modelo responde 429 con 'limit: 0', se prueba el siguiente candidato sin esperar."""
    import json as _json

    import requests

    from dealflow import extract

    usados = []

    class _R:
        def __init__(self, code, data=None, text=""):
            self.status_code, self.headers, self._d, self.text = code, {}, data, text

        def json(self):
            return self._d

    def _get(url, headers, timeout):
        return _R(200, {"data": [{"id": "models/gemini-omni-1.1-flash"}, {"id": "models/gemini-3.8-flash"},
                                 {"id": "models/gemini-3.5-flash"}, {"id": "models/gemini-3.5-flash-lite"}]})

    def _post(url, json, timeout, headers):
        usados.append(json["model"])
        if json["model"] == "gemini-3.8-flash":
            return _R(429, text="Quota exceeded for metric: free_tier_requests, limit: 0")
        args = {"startups": []}
        return _R(200, {"choices": [{"message": {"tool_calls": [
            {"function": {"name": "registrar_startups", "arguments": _json.dumps(args)}}]}}]})

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEALFLOW_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(config, "PAUSA_GEMINI_SEG", 0)
    monkeypatch.setattr(extract, "_MODELO_GEMINI", None)
    monkeypatch.setattr(extract, "_CANDIDATOS_GEMINI", None)
    art = {"fuente": "X", "fecha": "2026-10-20T00:00:00", "titulo": "T", "tags": [], "texto": "..."}
    assert extract.evaluar(art) == []
    assert usados == ["gemini-3.8-flash", "gemini-3.5-flash"]  # omni excluido; 3.8 sin cuota -> 3.5
    assert extract.modelo() == "gemini-3.5-flash"


def test_lote_reparte_por_nota():
    from dealflow import extract

    sts = [{"nota": 1, "nombre": "B"}, {"nota": 0, "nombre": "A"}, {"nota": 7, "nombre": "fuera"}, {"nombre": "sin"}]
    assert extract._repartir(sts, 2) == [[{"nombre": "A"}], [{"nombre": "B"}]]
    assert extract._repartir([{"nombre": "X"}], 1) == [[{"nombre": "X"}]]  # una sola nota: no requiere índice


def test_adquirida_se_descarta():
    c = scoring.clasificar(_s(nombre="Valida", adquirida=True))
    assert c["estado"] == "descartada" and "adquirida" in c["filtros_fallidos"]


def test_backtest_ignora_filtro_de_portafolio(monkeypatch, tmp_path):
    import backtest
    from dealflow import extract

    monkeypatch.setattr(backtest, "SALIDA", tmp_path / "bt.json")
    monkeypatch.setattr(backtest, "leer_nota", lambda url: {"fuente": "B", "url": url, "titulo": "t",
                                                           "texto": "x" * 400, "fecha": "2022-01-17", "tags": []})
    monkeypatch.setattr(extract, "modelo", lambda: "simulado")

    def _lote(notas):
        return [[_s(nombre=backtest_nombres.pop(0))] for _ in notas]

    casos = __import__("json").loads(backtest.CASOS.read_text(encoding="utf-8"))["casos"]
    backtest_nombres = [c["empresa"] for c in casos]
    monkeypatch.setattr(extract, "evaluar_lote", _lote)
    r = backtest.correr()
    assert r["con_nota_legible"] == len(casos)
    assert all(f["califica"] and "ya_en_portafolio" not in f["filtros_fallidos"] for f in r["filas"])
    assert r["recall"] == 1.0


def test_etapa_desconocida_queda_por_verificar():
    c = scoring.clasificar(_s(nombre="Sinetapa", etapa="desconocida"))
    assert c["estado"] == "por_verificar" and c["score"] == 100


def test_verificacion_humana_manda(monkeypatch):
    monkeypatch.setattr(scoring, "VERIFICACIONES", {"magnar": {"etapa": "serie_a_o_mas", "fuente": "u", "nota": "n"}})
    c = scoring.clasificar(_s(nombre="Magnar", etapa="desconocida"))
    assert c["estado"] == "descartada" and "etapa_tardia" in c["filtros_fallidos"]
    assert c["verificacion"]["fuente"] == "u"


def test_gemini_reintenta_tras_caida_de_red(monkeypatch):
    import json as _json

    import requests

    from dealflow import extract

    llamadas = {"n": 0}

    class _R:
        status_code, headers, text = 200, {}, ""

        def json(self):
            return {"choices": [{"message": {"tool_calls": [
                {"function": {"name": "registrar_startups", "arguments": _json.dumps({"startups": []})}}]}}]}

    def _post(url, json, timeout, headers):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise requests.ConnectionError("Remote end closed connection")
        return _R()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DEALFLOW_MODEL", "gemini-x")
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(config, "PAUSA_GEMINI_SEG", 0)
    monkeypatch.setattr("time.sleep", lambda s: None)
    art = {"fuente": "X", "fecha": "2026-10-20T00:00:00", "titulo": "T", "tags": [], "texto": "..."}
    assert extract.evaluar(art) == [] and llamadas["n"] == 2
