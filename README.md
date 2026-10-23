# Latin Leap · Dealflow Engine

Herramienta automatizada que origina oportunidades pre-seed y seed en LATAM hispanohablante y las califica contra la tesis publicada de Latin Leap. Corre sola cada lunes, publica un tablero y abre un digest semanal con las mejores oportunidades y un borrador de primer contacto para cada una.

- **Tablero:** https://isaacolimor.github.io/latinleap-dealflow/
- **Digests semanales:** pestaña *Issues* de este repositorio, etiqueta `dealflow-digest`
- **Ejecuciones:** pestaña *Actions*, workflow "Dealflow semanal"

## El problema y la meta

El baseline es de 60 deals al mes; la meta es al menos 6 adicionales (+10%). Para un fondo de USD 20M con equipo chico, el cuello de botella no es leer más notas sino encontrar las pocas que encajan con la tesis antes que otros. Por eso la herramienta optimiza calidad: de cada 10 notas de ecosistema, la mayoría son Serie B en adelante, empresas brasileñas, corporativos o adquisiciones.

La métrica de éxito tiene dos niveles:

1. **Calificadas nuevas por mes** (score ≥ 60, pasan todos los filtros). Es lo que la herramienta controla.
2. **Aceptadas por el equipo**: las que alguien del fondo marca en el digest para revisar. Es la que demuestra el +10% real, y sirve para recalibrar la rúbrica.

## Cómo funciona

```
Fuentes ──► Prefiltro ──► Extracción con IA ─────► Scorecard en Python ──► Digest + Tablero
 (RSS,       (sin costo:    (hechos + nivel 0/0.5/1    (filtros eliminatorios,     (issue semanal,
  scraping)   vistos,        por criterio, con          pesos de la tesis,          GitHub Pages)
              Serie B+,      evidencia textual)         umbral)                          │
              solo Brasil)                                                               ▼
                                                              decisiones del equipo (casillas) ──► métrica
```

| Fuente | Tipo | Por qué |
|---|---|---|
| LatamList | RSS | Rondas etiquetadas por país, sector y etapa |
| Startups Latam | RSS (español) | Rondas y perfiles de founders, incluye etapas muy tempranas |
| Descubre.vc | Scraping | Noticias diarias del ecosistema, operado por 500 Global |
| Google News | RSS de búsqueda | Señales tempranas: aceleradoras, pre-seed, rondas locales |

Fuentes evaluadas y descartadas: Product Hunt (sus términos prohíben uso comercial de la API sin permiso y no expone ubicación), El Referente (bloquea bots), feed de aceleradoras de LatamList (sin publicaciones desde 2024).

## Decisiones de diseño

- **El modelo no decide el score.** La IA extrae hechos y califica cada criterio en niveles discretos con evidencia; Python aplica filtros y pesos. Así el resultado es auditable y la rúbrica se recalibra sin volver a pagar llamadas al modelo. Es la misma lógica que una política de admisión de crédito.
- **Filtros antes que pesos.** País, etapa, tecnología, tipo de empresa y portafolio son eliminatorios. Ejemplo real: Dapta (portafolio de Latin Leap) apareció en el feed el 20 oct 2026 con un score bruto alto; sin el filtro de portafolio, la herramienta le habría recomendado al fondo su propia empresa.
- **Prefiltro gratis.** Notas ya vistas, fuera de ventana, Serie B o más y solo Brasil se descartan antes de llamar al modelo.
- **Etiqueta "levantando ahora"** separada del score, para distinguir timing de calidad del negocio.
- **Humano en el circuito.** La herramienta redacta el primer contacto, pero no lo envía. Escribir a founders a nombre del fondo es decisión del equipo.
- **Todo en GitHub.** Código, ejecución, datos, tablero y digest en un solo lugar, sin servidores ni bases de datos que mantener. Costo: cero con la capa gratuita de Gemini; con Claude, centavos de dólar por corrida.

La rúbrica completa está en [`rubrica.md`](rubrica.md) y sus parámetros en [`config.py`](config.py).

## Estructura

```
config.py                 rúbrica, países, sectores, fuentes, portafolio, umbral
run.py                    orquestador (python run.py | python run.py --demo)
dealflow/sources.py       recolección por fuente; una fuente caída no tumba la corrida
dealflow/extract.py       extracción estructurada con IA (Claude o Gemini, function calling)
dealflow/scoring.py       filtros, score ponderado y deduplicación
dealflow/store.py         persistencia en docs/data/*.json
dealflow/digest.py        issue semanal y lectura de casillas marcadas
docs/index.html           tablero (GitHub Pages)
.github/workflows/        ejecución automática semanal
tests/                    pruebas de la rúbrica y datos de ejemplo
```

## Correrlo

Automático: cada lunes a las 7:17 a.m. de Ciudad de México. Manual: *Actions* → "Dealflow semanal" → *Run workflow*.

Local:

```bash
pip install -r requirements.txt
python run.py --demo     # sin red ni API key; evaluaciones simuladas a mano sobre notas reales
export GEMINI_API_KEY=...   # o ANTHROPIC_API_KEY
python run.py            # corrida real
python -m pytest -q      # pruebas
```

Configuración del repositorio: un secreto con la llave de IA. `GEMINI_API_KEY` (capa gratuita de Google, se obtiene en aistudio.google.com) o `ANTHROPIC_API_KEY` (Claude, de pago; si existe, tiene prioridad). Opcionalmente, la variable `DEALFLOW_MODEL` fija un modelo específico.

## Limitaciones conocidas

- La lista de portafolio se infirió de los logos de latinleap.vc y hay que validarla con el fondo.
- Las noticias de rondas llegan tarde para pre-seed; la siguiente fuente a agregar son las listas de cohortes de aceleradoras (Start-Up Chile, Corfo y similares) y un formulario de aplicación para founders con el mismo scoring.
- Google News solo entrega título y medio, así que esas evaluaciones tienen menos evidencia.
- El scraping de Descubre.vc depende de su HTML actual.
