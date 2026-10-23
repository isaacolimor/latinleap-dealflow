"""Configuración del Latin Leap Dealflow Engine.

Todo lo que un analista del fondo querría ajustar sin tocar la lógica vive aquí:
pesos de la rúbrica, países, sectores, fuentes, portafolio y umbral.
"""

# ---------------------------------------------------------------------------
# Meta del reto
# ---------------------------------------------------------------------------
BASELINE_DEALS_MES = 60          # baseline dado por Latin Leap
META_INCREMENTO = 0.10           # +10%
META_DEALS_INCREMENTALES_MES = round(BASELINE_DEALS_MES * META_INCREMENTO)  # 6

# ---------------------------------------------------------------------------
# Rúbrica (v0.2, pesos definidos por Isaac el 22 oct 2026). Suman 100.
# ---------------------------------------------------------------------------
PESOS = {
    "sector": 20,
    "copycat": 20,
    "traccion": 20,
    "geografia": 15,
    "ai_first": 15,
    "momento": 5,
    "purpose": 5,
}
assert sum(PESOS.values()) == 100, "Los pesos de la rúbrica deben sumar 100"

UMBRAL_DIGEST = 60  # score mínimo para entrar al digest semanal

# Geografía: LATAM hispanohablante. Puntos sobre 15.
GEO_NUCLEO = {"México": 15, "Colombia": 15, "Chile": 15}
GEO_SECUNDARIO = {"Perú": 10, "Ecuador": 10, "Argentina": 10, "Uruguay": 10}
GEO_OTRO_HISPANO = {
    "Bolivia": 5, "Paraguay": 5, "Venezuela": 5, "Costa Rica": 5, "Panamá": 5,
    "Guatemala": 5, "Honduras": 5, "El Salvador": 5, "Nicaragua": 5,
    "República Dominicana": 5, "Puerto Rico": 5, "Cuba": 5,
}
PUNTOS_GEO = {**GEO_NUCLEO, **GEO_SECUNDARIO, **GEO_OTRO_HISPANO}
PAISES_VALIDOS = list(PUNTOS_GEO.keys())

# Sectores prioritarios según latinleap.vc (Latam Early Stage / Areas of Expertise)
SECTORES_PRIORITARIOS = [
    "fintech", "healthtech", "hrtech", "insurtech", "cleantech", "mobility",
    "next-gen retail", "foodtech", "ai saas",
]
SECTORES_OTROS = ["agtech", "proptech", "edtech", "logistica", "legaltech", "otro"]
SECTORES = SECTORES_PRIORITARIOS + SECTORES_OTROS

# Etapas que pasan el filtro (sweet spot "Pre- to Post-Seed")
ETAPAS_VALIDAS = ["idea_sin_ronda", "pre_seed", "seed", "post_seed_pre_a", "desconocida"]
ETAPAS = ETAPAS_VALIDAS + ["serie_a_o_mas"]

# ---------------------------------------------------------------------------
# Portafolio actual (para no recomendarle al fondo sus propias empresas).
# Inferido de los logos de latinleap.vc/portfolio el 22 oct 2026: VALIDAR con el fondo.
# ---------------------------------------------------------------------------
PORTAFOLIO = [
    "Dapta", "Netux", "Talkpush", "Vozy", "Talently", "Scircle", "Heva",
    "Agrizon", "TripWip", "Refurbi", "Keybe", "Alfred", "Luable", "Aivena",
    "Magneto", "Habi", "Stylework", "Zuzu Hospitality",
]

# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------
FUENTES_RSS = {
    "LatamList": "https://latamlist.com/feed/",
    "Startups Latam": "https://startupslatam.com/feed/",
}

# Google News RSS: señales tempranas (aceleradoras, pre-seed) en español.
# Solo título + fuente, por eso se tratan como señal más débil.
GOOGLE_NEWS_QUERIES = [
    "startup ronda pre-semilla",
    "startup levanta ronda semilla",
    "aceleradora selecciona startups",
    "startup mexicana levanta capital",
    "startup colombiana levanta capital",
    "startup chilena levanta capital",
]
GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={q}+when:14d&hl=es-419&gl=MX&ceid=MX:es-419"

DESCUBRE_PAGINAS = 3  # descubre.vc/noticias/0..N-1, 10 notas por página

# ---------------------------------------------------------------------------
# Operación
# ---------------------------------------------------------------------------
MODELO = "claude-sonnet-5-5"            # se usa si existe el secreto ANTHROPIC_API_KEY
# Si no hay llave de Anthropic: Gemini (capa gratuita de Google, llave en aistudio.google.com).
# El modelo Flash se descubre solo al arrancar; se puede fijar con la variable DEALFLOW_MODEL.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
PAUSA_GEMINI_SEG = 7             # ~10 consultas por minuto en la capa gratuita (verificar en AI Studio)
PRESUPUESTO_MINUTOS = 18        # deja de evaluar al cumplirse y guarda lo avanzado
MAX_ARTICULOS_POR_CORRIDA = 40   # tope por corrida (cuida el límite diario gratuito y el costo)
DIAS_VENTANA = 21                # ignora notas más viejas que esto

# Prefiltro barato antes de gastar una llamada al modelo
PREFILTRO_EXCLUIR = [
    "series b", "series c", "series d", "series e", "series f",
    "serie b", "serie c", "serie d", "serie e", "serie f",
    "ipo", "unicorn", "unicornio",
]
