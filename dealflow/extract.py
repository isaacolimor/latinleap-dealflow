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


_MODELO_GEMINI: str | None = None


def _descubrir_modelo_gemini(key: str) -> str:
    """Elige un modelo Flash disponible para esta llave, en vez de adivinar el nombre exacto."""
    import requests

    r = requests.get(config.GEMINI_URL + "models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {r.status_code} al listar modelos: {r.text[:300]}")
    ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
    flash = [i for i in ids if "flash" in i and not any(x in i for x in ("lite", "image", "tts", "audio", "live", "exp", "preview"))]
    if not flash:
        raise RuntimeError(f"No encontré un modelo Flash en Gemini. Disponibles: {ids[:15]}")
    return sorted(flash, reverse=True)[0]  # el de versión más alta


def modelo() -> str:
    global _MODELO_GEMINI
    if os.environ.get("DEALFLOW_MODEL"):
        return os.environ["DEALFLOW_MODEL"]
    if proveedor() == "anthropic":
        return config.MODELO
    if _MODELO_GEMINI is None:
        _MODELO_GEMINI = _descubrir_modelo_gemini(os.environ["GEMINI_API_KEY"])
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
    for intento in range(4):
        r = requests.post(config.GEMINI_URL + "chat/completions", json=cuerpo, timeout=120,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        if r.status_code in (429, 503) and intento < 3:  # límite gratuito o saturación: esperar y reintentar
            time.sleep(int(r.headers.get("retry-after", 30 * (intento + 1))))
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
    resp = cliente.messages.create(
        model=modelo(),
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
