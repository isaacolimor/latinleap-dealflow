"""Digest semanal como issue de GitHub + lectura de la retroalimentación del equipo.

Cada startup calificada aparece como una casilla. Cuando alguien del fondo marca
la casilla, la siguiente corrida la registra como "aceptada al pipeline". Esa es
la métrica que demuestra el +10%: deals que el equipo realmente decidió revisar.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests

import config

API = "https://api.github.com"
ETIQUETA = "dealflow-digest"
_CASILLA = re.compile(r"^- \[( |x|X)\] \*\*(.+?)\*\*", re.MULTILINE)


def _sesion() -> tuple[requests.Session, str] | tuple[None, None]:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None, None
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    return s, repo


def leer_decisiones(deals: dict) -> int:
    """Lee las casillas marcadas en digests anteriores. Devuelve cuántas decisiones nuevas registró."""
    s, repo = _sesion()
    if not s:
        return 0
    r = s.get(f"{API}/repos/{repo}/issues", params={"labels": ETIQUETA, "state": "all", "per_page": 50}, timeout=30)
    r.raise_for_status()
    por_nombre = {d["nombre"]: d for d in deals.values()}
    nuevas = 0
    for issue in r.json():
        for marca, nombre in _CASILLA.findall(issue.get("body") or ""):
            d = por_nombre.get(nombre)
            if d and marca.lower() == "x" and d.get("decision_equipo") != "aceptada":
                d["decision_equipo"] = "aceptada"
                d["fecha_decision"] = datetime.now(timezone.utc).isoformat()
                nuevas += 1
    return nuevas


def _bloque(d: dict) -> str:
    ds = d["desglose"]
    fuente = d["menciones"][0]
    analogo = d.get("copycat_analogo") or "sin análogo claro"
    etiqueta = " · 🔥 levantando ahora" if d.get("levantando_ahora") else ""
    return (
        f"- [ ] **{d['nombre']}** · {d['pais']} · {d['sector']} · score **{d['score']:.0f}**{etiqueta}\n"
        f"  {d['descripcion']}\n"
        f"  <details><summary>Detalle, evidencia y borrador de contacto</summary>\n\n"
        f"  - Etapa: {d['etapa']} {('· ' + d['ronda_reciente']) if d.get('ronda_reciente') else ''}\n"
        f"  - Desglose: sector {ds['sector']} · copycat {ds['copycat']} · tracción {ds['traccion']} · "
        f"geografía {ds['geografia']} · IA {ds['ai_first']} · momento {ds['momento']} · purpose {ds['purpose']}\n"
        f"  - Análogo: {analogo}\n"
        f"  - Evidencia: {d['evidencia']}\n"
        f"  - Fuente: [{fuente['fuente']}]({fuente['url']}) ({fuente['fecha'][:10]})\n\n"
        f"  > {d['borrador_contacto']}\n\n"
        f"  </details>\n"
    )


def publicar(nuevas: list[dict], metricas: dict, url_tablero: str) -> str | None:
    """Abre el issue del digest. Devuelve su URL, o None si no hay token o no hay nuevas."""
    s, repo = _sesion()
    if not s or not nuevas:
        return None
    s.post(f"{API}/repos/{repo}/labels", json={"name": ETIQUETA, "color": "D4E157"}, timeout=30)  # 422 si ya existe

    hoy = datetime.now(timezone.utc).strftime("%d %b %Y")
    nuevas = sorted(nuevas, key=lambda d: -d["score"])
    cuerpo = (
        f"**{len(nuevas)} oportunidades nuevas** sobre el umbral de {config.UMBRAL_DIGEST} puntos.\n\n"
        f"Marca la casilla de las que el equipo va a revisar. La siguiente corrida las registra como "
        f"deals aceptados y alimenta la métrica del tablero ({url_tablero}).\n\n"
        + "\n".join(_bloque(d) for d in nuevas)
        + f"\n---\nEmbudo de esta corrida: {metricas['articulos_nuevos']} notas nuevas → "
          f"{metricas['evaluados']} evaluadas con IA → {metricas['startups']} startups detectadas → "
          f"{metricas['pasan_filtros']} pasan filtros → {metricas['calificadas_nuevas']} calificadas.\n"
          f"_Generado automáticamente por Latin Leap Dealflow Engine._"
    )
    r = s.post(f"{API}/repos/{repo}/issues",
               json={"title": f"Dealflow semanal · {hoy} · {len(nuevas)} oportunidades", "body": cuerpo,
                     "labels": [ETIQUETA]}, timeout=30)
    r.raise_for_status()
    datos = r.json()
    for d in nuevas:
        d["issue"] = datos["number"]
    return datos["html_url"]
