"""Recalcula los puntajes guardados con la rúbrica actual de config.py, SIN volver a consultar a la IA.

Posible porque la IA solo extrae hechos y niveles (0 / 0.5 / 1); el puntaje lo calcula Python.
Útil cada vez que se ajustan pesos, sectores o filtros.

    python rescore.py
"""
from __future__ import annotations

import json

import config
from dealflow import scoring, store

CONSERVAR = ("key", "primera_vez", "ultima_vez", "menciones", "decision_equipo", "issue", "memo",
             "nota_revision", "fecha_decision")


def deals() -> list[tuple[str, float, float]]:
    actuales, vistos, corridas = store.cargar()
    cambios = []
    for key, d in actuales.items():
        antes = d.get("score_bruto", 0)
        nuevo = scoring.clasificar(d)
        conservar = {k: d[k] for k in CONSERVAR if k in d}
        d.clear()
        d.update({**nuevo, **conservar})
        if d["score_bruto"] != antes:
            cambios.append((d["nombre"], antes, d["score_bruto"]))
    store.guardar(actuales, vistos, corridas)
    return cambios


def backtest() -> list[tuple[str, float, float]]:
    p = store.DATA / "backtest.json"
    if not p.exists():
        return []
    r = json.loads(p.read_text(encoding="utf-8"))
    cambios = []
    for f in r["filas"]:
        if not f.get("detectada"):
            continue
        antes = f["score_bruto"]
        if f.get("campos"):  # backtests nuevos guardan los campos de la IA: reclasificación completa
            c = scoring.clasificar(f["campos"])
            f["desglose"], f["score_bruto"] = c["desglose"], c["score_bruto"]
            f["filtros_fallidos"] = [x for x in c["filtros_fallidos"] if x != "ya_en_portafolio"]
        else:  # backtest previo: solo se puede recalcular el criterio de sector
            f["desglose"]["sector"] = float(config.PESOS["sector"] if f.get("sector") in config.SECTORES_PRIORITARIOS else 0)
            f["score_bruto"] = round(sum(f["desglose"].values()), 1)
        f["score"] = f["score_bruto"] if not f["filtros_fallidos"] else 0.0
        f["califica"] = f["score"] >= config.UMBRAL_DIGEST
        if f["score_bruto"] != antes:
            cambios.append((f["empresa"], antes, f["score_bruto"]))
    ev = [f for f in r["filas"] if f.get("detectada")]
    r["califican"] = sum(f["califica"] for f in r["filas"])
    r["recall"] = round(r["califican"] / len(r["filas"]), 2)
    r["score_promedio"] = round(sum(f["score_bruto"] for f in ev) / len(ev), 1)
    r["promedio_por_criterio"] = {k: round(sum(f["desglose"].get(k, 0) for f in ev) / len(ev), 1) for k in config.PESOS}
    r["filas"].sort(key=lambda f: -f["score"])
    if cambios:  # registra el ajuste solo si movió algún puntaje
        r.setdefault("historial", []).append({"cambio": "recálculo con la rúbrica vigente en config.py",
                                              "recall_antes": None, "recall_despues": r["recall"]})
    p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    return cambios


if __name__ == "__main__":
    print("deals:", deals())
    print("backtest:", backtest())
