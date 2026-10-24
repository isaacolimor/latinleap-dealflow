"""Backtest de la rúbrica: ¿la herramienta habría detectado las inversiones reales de Latin Leap?

Toma notas de prensa de la ronda temprana de empresas del portafolio, las evalúa con el
mismo extractor y scorecard que la corrida semanal (ignorando solo el filtro de portafolio)
y guarda el resultado en docs/data/backtest.json para el tablero.

Advertencia metodológica: solo hay casos positivos (empresas en las que el fondo sí invirtió).
Esto mide si la herramienta las habría propuesto (recall), no si todo lo que propone es bueno.

    python backtest.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import config
from dealflow import extract, scoring

RAIZ = Path(__file__).resolve().parent
CASOS = RAIZ / "backtest" / "casos.json"
SALIDA = RAIZ / "docs" / "data" / "backtest.json"
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
             "Accept-Language": "es-MX,es;q=0.9,en;q=0.8"}


def leer_nota(url: str) -> dict | None:
    r = requests.get(url, headers=NAVEGADOR, timeout=30)
    if r.status_code >= 400:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    titulo = (soup.find("meta", property="og:title") or {}).get("content") or (soup.title.string if soup.title else "")
    cuerpo = soup.find("article") or soup
    parrafos = [p.get_text(" ", strip=True) for p in cuerpo.find_all("p")]
    texto = re.sub(r"\s+", " ", " ".join(p for p in parrafos if len(p) > 40))
    if len(texto) < 300:  # muro de pago o página vacía
        desc = (soup.find("meta", property="og:description") or {}).get("content", "")
        texto = (texto + " " + desc).strip()
    if len(texto) < 150:
        return None
    fecha = (soup.find("meta", property="article:published_time") or {}).get("content", "")
    return {"fuente": "Backtest", "url": url, "titulo": (titulo or "").strip(), "texto": texto,
            "fecha": fecha or datetime.now(timezone.utc).isoformat(), "tags": []}


def correr() -> dict:
    casos = json.loads(CASOS.read_text(encoding="utf-8"))["casos"]
    notas, sin_nota = [], []
    for c in casos:
        nota = None
        for url in c["urls"]:
            try:
                nota = leer_nota(url)
            except requests.RequestException:
                nota = None
            if nota:
                break
        if nota:
            notas.append((c, nota))
        else:
            sin_nota.append(c["empresa"])

    filas = []
    n = config.NOTAS_POR_CONSULTA
    for i in range(0, len(notas), n):
        lote = notas[i:i + n]
        resultados = extract.evaluar_lote([nota for _, nota in lote])
        for (caso, nota), startups in zip(lote, resultados):
            objetivo = scoring.normalizar(caso["empresa"])
            elegida = next((s for s in startups if objetivo in scoring.normalizar(s.get("nombre", ""))), None)
            if elegida is None:
                filas.append({"empresa": caso["empresa"], "ronda": caso["ronda"], "url": nota["url"],
                              "detectada": False, "score": 0, "califica": False,
                              "filtros_fallidos": ["no_identificada"], "desglose": {}})
                continue
            c = scoring.clasificar(elegida)
            fallas = [f for f in c["filtros_fallidos"] if f != "ya_en_portafolio"]
            score = c["score_bruto"] if not fallas else 0.0
            filas.append({"empresa": caso["empresa"], "ronda": caso["ronda"], "url": nota["url"],
                          "detectada": True, "score": score, "score_bruto": c["score_bruto"],
                          "califica": score >= config.UMBRAL_DIGEST, "filtros_fallidos": fallas,
                          "desglose": c["desglose"], "etapa": c.get("etapa"), "pais": c.get("pais"),
                          "sector": c.get("sector"), "copycat_analogo": c.get("copycat_analogo", ""),
                          "evidencia": c.get("evidencia", "")})

    evaluadas = [f for f in filas if f["detectada"]]
    resumen = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "modelo": extract.modelo(),
        "umbral": config.UMBRAL_DIGEST,
        "casos": len(casos),
        "con_nota_legible": len(notas),
        "sin_nota_legible": sin_nota,
        "califican": sum(f["califica"] for f in filas),
        "recall": round(sum(f["califica"] for f in filas) / len(filas), 2) if filas else None,
        "score_promedio": round(sum(f["score_bruto"] for f in evaluadas) / len(evaluadas), 1) if evaluadas else None,
        "promedio_por_criterio": {k: round(sum(f["desglose"].get(k, 0) for f in evaluadas) / len(evaluadas), 1)
                                  for k in config.PESOS} if evaluadas else {},
        "filas": sorted(filas, key=lambda f: -f["score"]),
    }
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


if __name__ == "__main__":
    r = correr()
    print(json.dumps({k: v for k, v in r.items() if k != "filas"}, ensure_ascii=False, indent=2))
    for f in r["filas"]:
        print(f"{f['empresa']:<10} score={f['score']:<5} califica={f['califica']} fallas={f['filtros_fallidos']}")
    sys.exit(0 if r["con_nota_legible"] else 1)
