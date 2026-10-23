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
    """Claude si hay ANTHROPIC_API_KEY; si no, GitHub Models (gratis dentro de GitHub Actions)."""
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "github"


def modelo() -> str:
    if os.environ.get("DEALFLOW_MODEL"):
        return os.environ["DEALFLOW_MODEL"]
    return config.MODELO if proveedor() == "anthropic" else config.MODELO_GITHUB


def _evaluar_github(articulo: dict) -> list[dict]:
    """GitHub Models: API compatible con OpenAI, autenticada con el GITHUB_TOKEN del workflow."""
    import time

    import requests

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Sin ANTHROPIC_API_KEY ni GITHUB_TOKEN: no hay modelo disponible")
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
        r = requests.post(config.GITHUB_MODELS_URL, json=cuerpo, timeout=90,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        if r.status_code == 429 and intento < 3:  # límite de uso gratuito: esperar y reintentar
            time.sleep(int(r.headers.get("retry-after", 20 * (intento + 1))))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub Models HTTP {r.status_code}: {r.text[:400]}")
        break
    llamadas = r.json()["choices"][0]["message"].get("tool_calls") or []
    for ll in llamadas:
        if ll["function"]["name"] == HERRAMIENTA["name"]:
            return json.loads(ll["function"]["arguments"]).get("startups", [])
    return []


def evaluar(articulo: dict, cliente=None) -> list[dict]:
    """Devuelve la lista de startups extraídas de un artículo."""
    if cliente is None and proveedor() == "github":
        return _evaluar_github(articulo)
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
