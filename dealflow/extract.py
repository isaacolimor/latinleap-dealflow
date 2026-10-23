"""Extracción y evaluación con Claude.

Diseño clave: el modelo NO calcula el score. Solo extrae hechos y califica cada
criterio en niveles discretos (0, 0.5, 1) con su evidencia textual. El score lo
calcula Python con los pesos de config.py. Así el resultado es auditable y la
rúbrica se puede recalibrar sin volver a llamar al modelo.
"""
from __future__ import annotations

import json
import os

import config

NIVEL = {"type": "number", "enum": [0, 0.5, 1]}

HERRAMIENTA = {
    "name": "registrar_startups",
    "description": "Registra cada startup mencionada en el artículo con hechos extraídos y calificación por criterio.",
    "input_schema": {
        "type": "object",
        "properties": {
            "startups": {
                "type": "array",
                "description": "Una entrada por empresa mencionada como protagonista. Lista vacía si el artículo no trata de ninguna startup.",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "sitio_web": {"type": "string", "description": "URL si aparece en el texto; si no, cadena vacía. No inventes."},
                        "pais": {"type": "string", "description": "País de sede u operación principal. 'Desconocido' si no se dice. 'Brasil', 'Estados Unidos', etc. si no es hispano."},
                        "es_startup": {"type": "boolean", "description": "False si es corporativo grande, fondo, aceleradora, banco o el comprador en una adquisición."},
                        "es_tecnologia": {"type": "boolean"},
                        "etapa": {"type": "string", "enum": config.ETAPAS},
                        "ronda_reciente": {"type": "string", "description": "Ej. 'Pre-seed US$500K, oct 2026'. Vacío si no hay."},
                        "sector": {"type": "string", "enum": config.SECTORES},
                        "descripcion": {"type": "string", "description": "Qué hace, en una frase en español."},
                        "ai_first": {**NIVEL, "description": "1 = IA es el núcleo del producto; 0.5 = usa IA relevante; 0 = sin IA."},
                        "copycat": {**NIVEL, "description": "1 = análogo exitoso claro en EE.UU./Europa/SE Asia; 0.5 = análogo parcial; 0 = sin análogo validado."},
                        "copycat_analogo": {"type": "string", "description": "Nombre del análogo extranjero y en qué se parece. Vacío si no hay."},
                        "traccion": {**NIVEL, "description": "1 = ingresos, clientes o usuarios reportados con cifras; 0.5 = piloto o tracción cualitativa; 0 = solo idea o nada reportado."},
                        "momento": {**NIVEL, "description": "1 = levantando ahora, recién salida de aceleradora o pre-seed muy reciente; 0.5 = seed cerrada hace poco; 0 = ronda cerrada hace >6 meses o sin señal."},
                        "purpose": {**NIVEL, "description": "1 = impacto explícito (inclusión, salud, sostenibilidad); 0.5 = indirecto; 0 = ninguno."},
                        "evidencia": {"type": "string", "description": "Máximo 2 frases: los datos del artículo que justifican las calificaciones."},
                        "borrador_contacto": {"type": "string", "description": "Correo breve (máx. 90 palabras) en español de un Associate de Latin Leap al founder: menciona algo específico de la nota y propone una llamada de 20 min. Sin halagos genéricos."},
                    },
                    "required": ["nombre", "pais", "es_startup", "es_tecnologia", "etapa", "sector", "descripcion",
                                 "ai_first", "copycat", "traccion", "momento", "purpose", "evidencia", "borrador_contacto"],
                },
            }
        },
        "required": ["startups"],
    },
}

SISTEMA = f"""Eres analista de originación de Latin Leap, un fondo de VC de US$20M que invierte en pre-seed y seed
en LATAM hispanohablante (núcleo: México, Colombia, Chile). Tesis publicada: empresas AI-first o AI-driven,
purpose-driven, con señales de product-market fit, preferentemente modelos ya probados en EE.UU., Europa o el
Sudeste Asiático ("proven copycat models"). Sectores prioritarios: {', '.join(config.SECTORES_PRIORITARIOS)}.

Lees una nota de prensa y registras SOLO las startups protagonistas de la nota.
Reglas estrictas:
- Usa únicamente lo que dice el texto. Si un dato no aparece, usa 'Desconocido', 'desconocida', cadena vacía o 0.
- No inventes sitios web, cifras ni análogos. Un análogo copycat debe ser una empresa extranjera real y conocida.
- Los países van en español con acento (México, Perú, Panamá).
- Si la nota trata de un corporativo, un fondo, un evento o una adquisición de una empresa grande, marca es_startup=false.
"""


def _texto_articulo(a: dict) -> str:
    tags = ", ".join(t for t in a.get("tags", []) if t)
    return f"Fuente: {a['fuente']}\nFecha: {a['fecha'][:10]}\nTítulo: {a['titulo']}\nEtiquetas: {tags}\n\n{a['texto']}"


def proveedor() -> str:
    """Claude si hay ANTHROPIC_API_KEY; si no, Gemini (capa gratuita de Google) si hay GEMINI_API_KEY."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    raise RuntimeError("No hay llave de IA: agrega el secreto GEMINI_API_KEY (gratis) o ANTHROPIC_API_KEY en GitHub")


ESPERAS = {"n": 0}  # cuántas veces el proveedor pidió esperar (para diagnóstico)
_MODELO_GEMINI: str | None = None
_CANDIDATOS_GEMINI: list[str] | None = None
_EXCLUIR = ("image", "tts", "audio", "live", "omni", "embedding", "thinking", "robotics", "computer")


def _preferencia(mid: str) -> tuple:
    """Ordena candidatos: Flash estándar de versión más alta primero, luego Flash-Lite, luego el resto."""
    import re

    ver = tuple(int(x) for x in re.findall(r"\d+", mid)[:2]) or (0,)
    es_lite = "lite" in mid
    es_preview = any(x in mid for x in ("preview", "exp"))
    return (es_preview, es_lite, tuple(-v for v in ver))


def _descubrir_modelos_gemini(key: str) -> list[str]:
    """Lista los modelos Flash disponibles para esta llave, ordenados por preferencia."""
    import requests

    r = requests.get(config.GEMINI_URL + "models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {r.status_code} al listar modelos: {r.text[:300]}")
    ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
    flash = [i for i in ids if i.startswith("gemini") and "flash" in i and not any(x in i for x in _EXCLUIR)]
    if not flash:
        raise RuntimeError(f"No encontré un modelo Flash en Gemini. Disponibles: {ids[:20]}")
    return sorted(flash, key=_preferencia)


def _descartar_modelo_actual() -> bool:
    """El modelo actual no tiene cuota gratuita: pasa al siguiente. False si ya no quedan."""
    global _MODELO_GEMINI
    if _CANDIDATOS_GEMINI:
        _CANDIDATOS_GEMINI.pop(0)
    _MODELO_GEMINI = _CANDIDATOS_GEMINI[0] if _CANDIDATOS_GEMINI else None
    return _MODELO_GEMINI is not None


def modelo() -> str:
    global _MODELO_GEMINI, _CANDIDATOS_GEMINI
    if os.environ.get("DEALFLOW_MODEL"):
        return os.environ["DEALFLOW_MODEL"]
    if proveedor() == "anthropic":
        return config.MODELO
    if _CANDIDATOS_GEMINI is None:
        _CANDIDATOS_GEMINI = _descubrir_modelos_gemini(os.environ["GEMINI_API_KEY"])
        _MODELO_GEMINI = _CANDIDATOS_GEMINI[0]
        print(f"[info] modelos Gemini candidatos: {_CANDIDATOS_GEMINI}")
    if _MODELO_GEMINI is None:
        raise RuntimeError("Ningún modelo Gemini disponible tiene cuota gratuita para esta llave")
    return _MODELO_GEMINI


def _evaluar_gemini(articulo: dict) -> list[dict]:
    """Gemini vía su API compatible con OpenAI (function calling)."""
    import time

    import requests

    key = os.environ["GEMINI_API_KEY"]
    herramienta = {"type": "function", "function": {
        "name": HERRAMIENTA["name"], "description": HERRAMIENTA["description"],
        "parameters": HERRAMIENTA["input_schema"]}}
    cuerpo = {
        "model": modelo(),
        "temperature": 0,
        "messages": [{"role": "system", "content": SISTEMA},
                     {"role": "user", "content": _texto_articulo(articulo)}],
        "tools": [herramienta],
        "tool_choice": {"type": "function", "function": {"name": HERRAMIENTA["name"]}},
    }
    intento = 0
    while True:
        cuerpo["model"] = modelo()
        r = requests.post(config.GEMINI_URL + "chat/completions", json=cuerpo, timeout=120,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        sin_cuota = r.status_code == 429 and "limit: 0" in r.text
        if sin_cuota and not os.environ.get("DEALFLOW_MODEL"):
            print(f"[info] {cuerpo['model']} no tiene cuota gratuita; probando el siguiente")
            if _descartar_modelo_actual():
                continue
            raise RuntimeError("Ningún modelo Gemini disponible tiene cuota gratuita para esta llave")
        intento += 1
        if r.status_code in (429, 503) and intento < 4:  # límite gratuito o saturación: esperar y reintentar
            ESPERAS["n"] += 1
            time.sleep(min(int(r.headers.get("retry-after", 15 * intento)), 60))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:400]}")
        break
    time.sleep(config.PAUSA_GEMINI_SEG)  # respeta ~10 consultas por minuto de la capa gratuita
    try:
        datos = r.json()
    except ValueError:
        raise RuntimeError(f"Respuesta no JSON (HTTP {r.status_code}): {r.text[:300]!r}") from None
    mensaje = datos["choices"][0]["message"]
    for ll in mensaje.get("tool_calls") or []:
        if ll["function"]["name"] == HERRAMIENTA["name"]:
            args = ll["function"].get("arguments") or ""
            try:
                return (json.loads(args) if isinstance(args, str) else args).get("startups", [])
            except ValueError:
                raise RuntimeError(f"Argumentos no JSON: {args[:300]!r}") from None
    texto = (mensaje.get("content") or "").strip().removeprefix("```json").removesuffix("```").strip()
    if texto.startswith("{"):
        return json.loads(texto).get("startups", [])
    return []


def evaluar(articulo: dict, cliente=None) -> list[dict]:
    """Devuelve la lista de startups extraídas de un artículo."""
    if cliente is None and proveedor() == "gemini":
        return _evaluar_gemini(articulo)
    if cliente is None:
        import anthropic
        cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        nombre_modelo = modelo()
    else:  # cliente inyectado (modo demo o pruebas): no requiere llave
        nombre_modelo = os.environ.get("DEALFLOW_MODEL") or config.MODELO
    resp = cliente.messages.create(
        model=nombre_modelo,
        max_tokens=2500,
        system=SISTEMA,
        tools=[HERRAMIENTA],
        tool_choice={"type": "tool", "name": HERRAMIENTA["name"]},
        messages=[{"role": "user", "content": _texto_articulo(articulo)}],
    )
    for bloque in resp.content:
        if getattr(bloque, "type", "") == "tool_use":
            datos = bloque.input
            if isinstance(datos, str):
                datos = json.loads(datos)
            return datos.get("startups", [])
    return []
