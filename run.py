"""Orquestador del Latin Leap Dealflow Engine.

Uso:
    python run.py              # corrida real (requiere ANTHROPIC_API_KEY; GITHUB_TOKEN para el digest)
    python run.py --demo       # corrida offline con artículos de ejemplo y un evaluador simulado
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from dealflow import digest, extract, scoring, sources, store

URL_TABLERO = os.environ.get("DEALFLOW_DASHBOARD_URL", "https://isaacolimor.github.io/latinleap-dealflow/")
_HISPANOS = [p.lower() for p in config.PAISES_VALIDOS] + [
    "mexican", "colombian", "chilean", "peruvian", "argentine", "argentinian", "uruguayan", "ecuadorian",
    "mexicana", "colombiana", "chilena", "peruana", "argentina", "uruguaya", "ecuatoriana", "mexico", "peru",
]


def prefiltro(a: dict, vistos: set, limite: datetime) -> str | None:
    """Descarta barato, antes de gastar una llamada al modelo. Devuelve el motivo o None si pasa."""
    if not a["url"] or a["url"] in vistos:
        return "ya_visto"
    try:
        if datetime.fromisoformat(a["fecha"]) < limite:
            return "fuera_de_ventana"
    except ValueError:
        pass
    texto = (a["titulo"] + " " + " ".join(a["tags"])).lower()
    if any(k in texto for k in config.PREFILTRO_EXCLUIR):
        return "etapa_tardia"
    brasil = any(k in texto for k in ("brazil", "brasil", "brazilian", "brasileña", "brasileño"))
    if brasil and not any(h in texto for h in _HISPANOS):
        return "solo_brasil"
    return None


def _modelo_seguro() -> str:
    try:
        return extract.modelo()
    except Exception:
        return "sin modelo configurado"


def corrida(cliente=None, articulos: list[dict] | None = None, estado_fuentes: dict | None = None) -> dict:
    deals, vistos, corridas = store.cargar()
    decisiones = digest.leer_decisiones(deals)

    if articulos is None:
        articulos, estado_fuentes = sources.recolectar()
    limite = datetime.now(timezone.utc) - timedelta(days=config.DIAS_VENTANA)

    por_fuente: dict[str, dict] = {}
    candidatos, descartes = [], {}
    for a in articulos:
        f = por_fuente.setdefault(a["fuente"], {"leidos": 0, "evaluados": 0, "startups": 0,
                                                "pasan_filtros": 0, "calificadas_nuevas": 0})
        f["leidos"] += 1
        motivo = prefiltro(a, vistos, limite)
        if motivo:
            descartes[motivo] = descartes.get(motivo, 0) + 1
            if motivo != "ya_visto":
                vistos.add(a["url"])
            continue
        if a["url"] not in {c["url"] for c in candidatos}:
            candidatos.append(a)

    # Alterna entre fuentes (más recientes primero) para que ninguna acapare el tope.
    # Lo que exceda el tope queda sin marcar como visto y se evalúa en la siguiente corrida.
    por_f: dict[str, list] = {}
    for a in sorted(candidatos, key=lambda x: x["fecha"], reverse=True):
        por_f.setdefault(a["fuente"], []).append(a)
    alternados = []
    while any(por_f.values()):
        for lista in por_f.values():
            if lista:
                alternados.append(lista.pop(0))
    candidatos = alternados[: config.MAX_ARTICULOS_POR_CORRIDA]
    nuevas_calificadas, errores, muestra_errores = [], 0, []
    inicio, pendientes_por_tiempo = time.monotonic(), 0
    for i, a in enumerate(candidatos):
        if time.monotonic() - inicio > config.PRESUPUESTO_MINUTOS * 60:
            pendientes_por_tiempo = len(candidatos) - i  # quedan sin marcar: se evalúan la próxima vez
            print(f"[info] presupuesto de tiempo agotado; {pendientes_por_tiempo} notas quedan para la siguiente corrida")
            break
        f = por_fuente[a["fuente"]]
        try:
            extraidas = extract.evaluar(a, cliente)
        except Exception as exc:
            # No se marca como vista: se reintenta en la siguiente corrida.
            errores += 1
            msg = f"{type(exc).__name__}: {exc}"[:600]
            if len(muestra_errores) < 3:
                muestra_errores.append({"url": a["url"], "error": msg})
            print(f"[error] {a['url']}: {msg}", file=sys.stderr)
            if errores >= 3 and f["evaluados"] == 0 and len(muestra_errores) == errores:
                print("[error] las primeras 3 evaluaciones fallaron; se detiene para no gastar cuota", file=sys.stderr)
                break
            continue
        vistos.add(a["url"])
        f["evaluados"] += 1
        for s in extraidas:
            c = scoring.clasificar(s)
            f["startups"] += 1
            f["pasan_filtros"] += int(not c["filtros_fallidos"])
            estado_previo = deals.get(scoring.normalizar(c.get("nombre", "")), {}).get("estado")
            key, _ = store.integrar(deals, c, a)
            d = deals.get(key)
            if d and d["estado"] == "calificada" and estado_previo != "calificada" and not d.get("issue"):
                nuevas_calificadas.append(d)
                f["calificadas_nuevas"] += 1

    metricas = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "modelo": _modelo_seguro(),
        "articulos_leidos": len(articulos),
        "articulos_nuevos": len(candidatos) + sum(v for k, v in descartes.items() if k != "ya_visto"),
        "descartes_prefiltro": descartes,
        "evaluados": sum(f["evaluados"] for f in por_fuente.values()),
        "errores_evaluacion": errores,
        "muestra_errores": muestra_errores,
        "pendientes_por_tiempo": pendientes_por_tiempo,
        "esperas_por_limite": extract.ESPERAS["n"],
        "minutos_evaluando": round((time.monotonic() - inicio) / 60, 1),
        "startups": sum(f["startups"] for f in por_fuente.values()),
        "pasan_filtros": sum(f["pasan_filtros"] for f in por_fuente.values()),
        "calificadas_nuevas": len(nuevas_calificadas),
        "decisiones_registradas": decisiones,
        "por_fuente": por_fuente,
        "estado_fuentes": estado_fuentes or {},
    }
    url_issue = None
    try:
        url_issue = digest.publicar(nuevas_calificadas, metricas, URL_TABLERO)
    except Exception as exc:
        print(f"[error] no se pudo publicar el digest: {exc}", file=sys.stderr)
    metricas["digest"] = url_issue

    corridas.append(metricas)
    store.guardar(deals, vistos, corridas)
    return metricas


def salud(m: dict) -> int:
    """Código de salida: 1 si hubo notas para evaluar y ninguna se pudo evaluar (la corrida se marca en rojo)."""
    return 1 if m["errores_evaluacion"] and m["evaluados"] == 0 else 0


def _modo_demo():
    """Evaluador simulado: permite probar todo el flujo sin red ni API key."""
    fixtures = json.loads((Path(__file__).parent / "tests" / "fixtures" / "demo.json").read_text(encoding="utf-8"))

    class _Bloque:
        type = "tool_use"

        def __init__(self, data):
            self.input = data

    class _Resp:
        def __init__(self, data):
            self.content = [_Bloque(data)]

    class _Cliente:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                titulo = kw["messages"][0]["content"].split("Título: ")[1].split("\n")[0]
                return _Resp({"startups": fixtures["respuestas"].get(titulo, [])})

    ahora = datetime.now(timezone.utc).isoformat()
    for a in fixtures["articulos"]:
        a["fecha"] = ahora
    return _Cliente(), fixtures["articulos"], {"demo": {"ok": True, "articulos": len(fixtures["articulos"])}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="corrida offline con datos de ejemplo")
    args = ap.parse_args()
    if args.demo:
        cliente, arts, estado = _modo_demo()
        m = corrida(cliente, arts, estado)
    else:
        m = corrida()
    print(json.dumps({k: v for k, v in m.items() if k != "por_fuente"}, ensure_ascii=False, indent=2))
    sys.exit(salud(m))
