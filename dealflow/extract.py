"""Extracción y evaluación con IA (Claude o Gemini).

Las notas se mandan en lotes (varias por consulta) para cuidar la cuota gratuita.

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
    "description": "Registra cada startup protagonista de las notas, con hechos extraídos y calificación por criterio.",
    "input_schema": {
        "type": "object",
        "properties": {
            "startups": {
                "type": "array",
                "description": "Una entrada por startup protagonista. Las notas sin startups no generan entradas.",
                "items": {
                    "type": "object",
                    "properties": {
                        "nota": {"type": "integer", "description": "Número de la nota de donde sale esta startup (el N de '### Nota N')."},
                        "nombre": {"type": "string"},
                        "sitio_web": {"type": "string", "description": "URL si aparece en el texto; si no, cadena vacía. No inventes."},
                        "pais": {"type": "string", "description": "País de sede u operación principal. 'Desconocido' si no se dice. 'Brasil', 'Estados Unidos', etc. si no es hispano."},
                        "es_startup": {"type": "boolean", "description": "False si es corporativo grande, fondo, aceleradora, banco o el comprador en una adquisición."},
                        "es_tecnologia": {"type": "boolean"},
                        "adquirida": {"type": "boolean", "description": "True si la nota dice que la empresa fue comprada, se fusionó o cerró. Una empresa adquirida ya no es un deal."},
                        "etapa": {"type": "string", "enum": config.ETAPAS},
                        "ronda_reciente": {"type": "string", "description": "Ej. 'Pre-seed US$500K, oct 2026'. Vacío si no hay."},
                        "sector": {"type": "string", "enum": config.SECTORES},
                        "descripcion": {"type": "string", "description": "Qué hace, en una frase en español."},
                        "ai_first": {**NIVEL, "description": "1 = IA es el núcleo del producto; 0.5 = usa IA relevante; 0 = sin IA."},
                        "copycat": {**NIVEL, "description": "1 SOLO si el modelo de negocio es esencialmente el mismo que el de una empresa extranjera exitosa y conocida (mismo cliente, mismo producto, mismo modelo de ingresos) que la startup replica en LATAM; 0.5 = parecido parcial (mismo sector o problema, distinto producto); 0 = sin análogo claro. Sé estricto: compartir sector no basta."},
                        "copycat_analogo": {"type": "string", "description": "Nombre del análogo extranjero y en qué se parece. Vacío si no hay."},
                        "traccion": {**NIVEL, "description": "1 = ingresos, clientes o usuarios reportados con cifras; 0.5 = piloto o tracción cualitativa; 0 = solo idea o nada reportado."},
                        "momento": {**NIVEL, "description": "1 = levantando ahora, recién salida de aceleradora o pre-seed muy reciente; 0.5 = seed cerrada hace poco; 0 = ronda cerrada hace >6 meses o sin señal."},
                        "purpose": {**NIVEL, "description": "1 = impacto explícito (inclusión, salud, sostenibilidad); 0.5 = indirecto; 0 = ninguno."},
                        "evidencia": {"type": "string", "description": "Máximo 2 frases: los datos del artículo que justifican las calificaciones."},
                        "borrador_contacto": {"type": "string", "description": "Correo breve (máx. 90 palabras) en español de un Associate de Latin Leap al founder: menciona algo específico de la nota y propone una llamada de 20 min. Sin halagos genéricos."},
                    },
                    "required": ["nota", "nombre", "pais", "es_startup", "es_tecnologia", "adquirida", "etapa", "sector", "descripcion",
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

Recibes varias notas de prensa numeradas. Por cada nota, registras SOLO las startups protagonistas,
indicando en "nota" el número de la nota de donde salen. Evalúa cada nota de forma independiente.
Reglas estrictas:
- Usa únicamente lo que dice el texto. Si un dato no aparece, usa 'Desconocido', 'desconocida', cadena vacía o 0.
- No inventes sitios web, cifras ni análogos. Un análogo copycat debe ser una empresa extranjera real y conocida.
- Los países van en español con acento (México, Perú, Panamá).
- Si la nota trata de un corporativo, un fondo, un evento o una adquisición de una empresa grande, marca es_startup=false.
- Etapa: si la nota no la dice, infiérela de señales. Millones de usuarios, operación en varios países con
  equipos grandes, más de ~5 años operando o capital acumulado mayor a ~US$5M indican 'serie_a_o_mas'.
  Usa 'desconocida' solo si de verdad no hay señales.
"""


def _texto_articulo(a: dict, max_chars: int = 2500) -> str:
    tags = ", ".join(t for t in a.get("tags", []) if t)
    return (f"Fuente: {a['fuente']}\nFecha: {a['fecha'][:10]}\nTítulo: {a['titulo']}\nEtiquetas: {tags}\n\n"
            f"{a['texto'][:max_chars]}")


def _texto_lote(articulos: list[dict]) -> str:
    return "\n\n".join(f"### Nota {i}\n{_texto_articulo(a)}" for i, a in enumerate(articulos))


def _repartir(startups: list[dict], n: int) -> list[list[dict]]:
    """Asigna cada startup a su nota según el campo 'nota'."""
    salida: list[list[dict]] = [[] for _ in range(n)]
    for st in startups:
        try:
            i = int(st.pop("nota", 0 if n == 1 else -1))
        except (TypeError, ValueError):
            i = -1
        if 0 <= i < n:
            salida[i].append(st)
    return salida


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


def _llamar_gemini(contenido: str, herramienta_def: dict = HERRAMIENTA, sistema: str = SISTEMA) -> dict:
    """Gemini vía su API compatible con OpenAI (function calling)."""
    import time

    import requests

    key = os.environ["GEMINI_API_KEY"]
    herramienta = {"type": "function", "function": {
        "name": herramienta_def["name"], "description": herramienta_def["description"],
        "parameters": herramienta_def["input_schema"]}}
    cuerpo = {
        "model": modelo(),
        "temperature": 0,
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": contenido}],
        "tools": [herramienta],
        "tool_choice": {"type": "function", "function": {"name": herramienta_def["name"]}},
    }
    intento, fallas_red = 0, 0
    while True:
        cuerpo["model"] = modelo()
        try:
            r = requests.post(config.GEMINI_URL + "chat/completions", json=cuerpo, timeout=config.TIMEOUT_GEMINI_SEG,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except (requests.ConnectionError, requests.Timeout) as exc:
            # Caída o lentitud momentánea del servidor: reintentar con espera; si persiste, probar otro modelo.
            fallas_red += 1
            ESPERAS["n"] += 1
            print(f"[info] {cuerpo['model']}: {type(exc).__name__}; reintento {fallas_red}")
            if fallas_red in (1, 3):
                time.sleep(20 * fallas_red)
                continue
            if fallas_red == 2 and not os.environ.get("DEALFLOW_MODEL") and _descartar_modelo_actual():
                continue
            raise
        # Cuota agotada para este modelo (límite 0, o la cuota diaria): cada modelo tiene la suya, se pasa al siguiente.
        agotada = r.status_code == 429 and ("limit: 0" in r.text or "PerDay" in r.text or (intento >= 2))
        if agotada and not os.environ.get("DEALFLOW_MODEL"):
            print(f"[info] {cuerpo['model']} sin cuota disponible; probando el siguiente modelo")
            if _descartar_modelo_actual():
                intento = 0
                continue
            raise RuntimeError("Todos los modelos Gemini agotaron su cuota gratuita por hoy: " + r.text[:300])
        intento += 1
        if r.status_code in (429, 503) and intento < 4:  # límite por minuto o saturación: esperar y reintentar
            ESPERAS["n"] += 1
            time.sleep(min(int(r.headers.get("retry-after", 20 * intento)), 60))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:400]}")
        break
    time.sleep(config.PAUSA_GEMINI_SEG)  # respeta el límite de consultas por minuto de la capa gratuita
    try:
        datos = r.json()
    except ValueError:
        raise RuntimeError(f"Respuesta no JSON (HTTP {r.status_code}): {r.text[:300]!r}") from None
    mensaje = datos["choices"][0]["message"]
    for ll in mensaje.get("tool_calls") or []:
        if ll["function"]["name"] == herramienta_def["name"]:
            args = ll["function"].get("arguments") or ""
            try:
                return json.loads(args) if isinstance(args, str) else args
            except ValueError:
                raise RuntimeError(f"Argumentos no JSON: {args[:300]!r}") from None
    texto = (mensaje.get("content") or "").strip().removeprefix("```json").removesuffix("```").strip()
    if texto.startswith("{"):
        return json.loads(texto)
    return {}


def _llamar_anthropic(contenido: str, herramienta_def: dict, sistema: str, cliente=None) -> dict:
    if cliente is None:
        import anthropic
        cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        nombre_modelo = modelo()
    else:  # cliente inyectado (modo demo o pruebas): no requiere llave
        nombre_modelo = os.environ.get("DEALFLOW_MODEL") or config.MODELO
    resp = cliente.messages.create(
        model=nombre_modelo, max_tokens=8000, system=sistema, tools=[herramienta_def],
        tool_choice={"type": "tool", "name": herramienta_def["name"]},
        messages=[{"role": "user", "content": contenido}],
    )
    for bloque in resp.content:
        if getattr(bloque, "type", "") == "tool_use":
            return json.loads(bloque.input) if isinstance(bloque.input, str) else bloque.input
    return {}


def _llamar(contenido: str, herramienta_def: dict, sistema: str, cliente=None) -> dict:
    if cliente is None and proveedor() == "gemini":
        return _llamar_gemini(contenido, herramienta_def, sistema)
    return _llamar_anthropic(contenido, herramienta_def, sistema, cliente)


def evaluar_lote(articulos: list[dict], cliente=None) -> list[list[dict]]:
    """Evalúa varias notas en una sola consulta. Devuelve, por cada nota, su lista de startups."""
    datos = _llamar(_texto_lote(articulos), HERRAMIENTA, SISTEMA, cliente)
    return _repartir(datos.get("startups", []), len(articulos))


HERRAMIENTA_MEMO = {
    "name": "registrar_memos",
    "description": "Registra un mini memo de primera llamada por cada startup.",
    "input_schema": {
        "type": "object",
        "properties": {
            "memos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string", "description": "Nombre exacto de la startup tal como se recibió."},
                        "tesis": {"type": "string", "description": "Por qué podría encajar en Latin Leap, en una frase."},
                        "riesgos": {"type": "array", "items": {"type": "string"}, "description": "Los 3 riesgos principales, una frase cada uno."},
                        "preguntas": {"type": "array", "items": {"type": "string"}, "description": "3 preguntas concretas para la primera llamada con el founder, que despejen esos riesgos."},
                    },
                    "required": ["nombre", "tesis", "riesgos", "preguntas"],
                },
            }
        },
        "required": ["memos"],
    },
}

SISTEMA_MEMO = """Eres Associate de Latin Leap (VC pre-seed y seed en LATAM hispanohablante; tesis AI-first,
purpose-driven, modelos probados en otros mercados). Para cada startup recibida, prepara un mini memo para la
primera llamada: tesis en una frase, 3 riesgos y 3 preguntas específicas que un comité haría (unit economics,
tamaño de mercado, defensibilidad, equipo, regulación, uso de fondos). Basa todo en los datos recibidos; si falta
información, conviértelo en pregunta en lugar de suponer. Escribe en español, sin halagos."""


def generar_memos(deals: list[dict], cliente=None) -> dict[str, dict]:
    """Una sola consulta para todas las startups calificadas de la corrida. Devuelve {nombre: memo}."""
    if not deals:
        return {}
    fichas = "\n\n".join(
        f"### {d['nombre']}\nPaís: {d.get('pais')} · Sector: {d.get('sector')} · Etapa: {d.get('etapa')} · "
        f"Ronda: {d.get('ronda_reciente') or 'sin dato'}\nQué hace: {d.get('descripcion')}\n"
        f"Evidencia: {d.get('evidencia')}\nAnálogo: {d.get('copycat_analogo') or 'sin análogo claro'}\n"
        f"Score: {d.get('score')} ({d.get('desglose')})" for d in deals)
    datos = _llamar(fichas, HERRAMIENTA_MEMO, SISTEMA_MEMO, cliente)
    por_nombre = {d["nombre"]: d for d in deals}
    salida = {}
    for m in datos.get("memos", []):
        if m.get("nombre") in por_nombre:
            salida[m["nombre"]] = {"tesis": m.get("tesis", ""), "riesgos": m.get("riesgos", [])[:3],
                                   "preguntas": m.get("preguntas", [])[:3]}
    return salida


def evaluar(articulo: dict, cliente=None) -> list[dict]:
    """Atajo para una sola nota."""
    return evaluar_lote([articulo], cliente)[0]
