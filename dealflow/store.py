"""Persistencia en JSON dentro del repo (docs/data), para que el tablero en
GitHub Pages lea exactamente lo mismo que produjo la última corrida."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dealflow.scoring import normalizar

DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
DEALS = DATA / "deals.json"
VISTOS = DATA / "vistos.json"
CORRIDAS = DATA / "corridas.json"


def _leer(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _escribir(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cargar() -> tuple[dict, set, list]:
    deals = {d["key"]: d for d in _leer(DEALS, [])}
    return deals, set(_leer(VISTOS, [])), _leer(CORRIDAS, [])


def guardar(deals: dict, vistos: set, corridas: list) -> None:
    orden = sorted(deals.values(), key=lambda d: (-d.get("score", 0), d.get("nombre", "")))
    _escribir(DEALS, orden)
    _escribir(VISTOS, sorted(vistos))
    _escribir(CORRIDAS, corridas)


def integrar(deals: dict, startup: dict, articulo: dict) -> tuple[str, bool]:
    """Agrega o actualiza un deal. Devuelve (key, es_nuevo)."""
    key = normalizar(startup.get("nombre", ""))
    if not key:
        return "", False
    ahora = datetime.now(timezone.utc).isoformat()
    mencion = {"fuente": articulo["fuente"], "url": articulo["url"],
               "titulo": articulo["titulo"], "fecha": articulo["fecha"]}

    if key not in deals:
        deals[key] = {**startup, "key": key, "primera_vez": ahora, "ultima_vez": ahora,
                      "menciones": [mencion], "decision_equipo": None, "issue": None}
        return key, True

    previo = deals[key]
    if all(m["url"] != mencion["url"] for m in previo["menciones"]):
        previo["menciones"].append(mencion)
    previo["ultima_vez"] = ahora
    # Si la nueva evaluación es mejor informada (score bruto mayor), actualiza los campos evaluados,
    # sin perder historia, decisión del equipo ni fecha de primera detección.
    if startup.get("score_bruto", 0) > previo.get("score_bruto", 0):
        conservar = {k: previo[k] for k in ("key", "primera_vez", "menciones", "decision_equipo", "issue", "ultima_vez")}
        estado_previo = previo.get("estado")
        previo.clear()
        previo.update({**startup, **conservar})
        if estado_previo == "calificada" and previo["estado"] != "portafolio":
            previo["estado"] = "calificada"
    return key, False
