"""Scorecard determinista: filtros eliminatorios + criterios ponderados.

Funciona como una política de admisión de crédito: primero reglas de exclusión,
después un score ponderado, y un umbral que se recalibra con resultados.
"""
from __future__ import annotations

import re
import unicodedata

import config


def normalizar(nombre: str) -> str:
    """Llave de deduplicación: minúsculas, sin acentos, sin sufijos corporativos."""
    n = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(inc|llc|sas|s\.a\.s|sa de cv|s\.a\.|spa|ltda|corp|technologies|technology|tech)\b", "", n)
    n = re.sub(r"\.(ai|io|app|co|com|mx|vc)$", "", n.strip())
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


PORTAFOLIO_NORM = {normalizar(p) for p in config.PORTAFOLIO}
_PAISES_NORM = {normalizar(p): p for p in config.PAISES_VALIDOS}


def pais_canonico(pais: str) -> str | None:
    return _PAISES_NORM.get(normalizar(pais))


def evaluar_filtros(s: dict) -> list[str]:
    """Devuelve la lista de filtros que NO pasa (vacía = pasa todos)."""
    fallas = []
    if not s.get("es_startup", False):
        fallas.append("no_es_startup")
    if not s.get("es_tecnologia", False):
        fallas.append("no_es_tecnologia")
    if pais_canonico(s.get("pais", "")) is None:
        fallas.append("fuera_de_geografia")
    if s.get("etapa") not in config.ETAPAS_VALIDAS:
        fallas.append("etapa_tardia")
    if normalizar(s.get("nombre", "")) in PORTAFOLIO_NORM:
        fallas.append("ya_en_portafolio")
    return fallas


def calcular_score(s: dict) -> tuple[float, dict]:
    """Score 0 a 100 y desglose por criterio (puntos obtenidos)."""
    p = config.PESOS
    pais = pais_canonico(s.get("pais", ""))
    desglose = {
        "sector": p["sector"] * (1.0 if s.get("sector") in config.SECTORES_PRIORITARIOS else 0.0),
        "copycat": p["copycat"] * float(s.get("copycat", 0)),
        "traccion": p["traccion"] * float(s.get("traccion", 0)),
        "geografia": float(config.PUNTOS_GEO.get(pais, 0)) if pais else 0.0,
        "ai_first": p["ai_first"] * float(s.get("ai_first", 0)),
        "momento": p["momento"] * float(s.get("momento", 0)),
        "purpose": p["purpose"] * float(s.get("purpose", 0)),
    }
    desglose = {k: round(v, 1) for k, v in desglose.items()}
    return round(sum(desglose.values()), 1), desglose


def clasificar(s: dict) -> dict:
    """Agrega filtros, score, desglose y estado a una startup extraída."""
    fallas = evaluar_filtros(s)
    score, desglose = calcular_score(s)
    if "ya_en_portafolio" in fallas:
        estado = "portafolio"
    elif fallas:
        estado = "descartada"
    elif score >= config.UMBRAL_DIGEST:
        estado = "calificada"
    else:
        estado = "bajo_umbral"
    pais = pais_canonico(s.get("pais", ""))
    return {
        **s,
        "pais": pais or s.get("pais", "Desconocido"),
        "filtros_fallidos": fallas,
        "score": score if not fallas else 0.0,
        "score_bruto": score,
        "desglose": desglose,
        "estado": estado,
        "levantando_ahora": float(s.get("momento", 0)) == 1.0,
    }
