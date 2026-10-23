"""Fuentes de señales. Cada fuente devuelve una lista de artículos normalizados:

    {"fuente", "url", "titulo", "texto", "fecha" (ISO), "tags"}

Si una fuente falla, se registra el error y el pipeline sigue con las demás.
"""
from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

import config

HEADERS = {"User-Agent": "LatinLeapDealflowBot/1.0 (+https://github.com/isaacolimor/latinleap-dealflow)"}
TIMEOUT = 25


def _limpiar_html(texto: str, max_chars: int = 4000) -> str:
    if not texto:
        return ""
    soup = BeautifulSoup(texto, "html.parser")
    limpio = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    limpio = re.sub(r"The post .*? appeared first on .*?\.$", "", limpio).strip()
    return limpio[:max_chars]


def _fecha_iso(entry) -> str:
    for campo in ("published_parsed", "updated_parsed"):
        valor = entry.get(campo)
        if valor:
            return datetime(*valor[:6], tzinfo=timezone.utc).isoformat()
    for campo in ("published", "updated"):
        valor = entry.get(campo)
        if valor:
            try:
                return parsedate_to_datetime(valor).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass
    return datetime.now(timezone.utc).isoformat()


def _desde_feed(nombre: str, contenido: bytes | str) -> list[dict]:
    feed = feedparser.parse(contenido)
    articulos = []
    for e in feed.entries:
        cuerpo = ""
        if e.get("content"):
            cuerpo = e["content"][0].get("value", "")
        cuerpo = cuerpo or e.get("summary", "")
        articulos.append({
            "fuente": nombre,
            "url": e.get("link", ""),
            "titulo": html.unescape(e.get("title", "")).strip(),
            "texto": _limpiar_html(cuerpo),
            "fecha": _fecha_iso(e),
            "tags": [t.get("term", "") for t in e.get("tags", [])],
        })
    return articulos


def rss(nombre: str, url: str) -> list[dict]:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return _desde_feed(nombre, r.content)


def google_news() -> list[dict]:
    articulos, fallas = [], []
    for q in config.GOOGLE_NEWS_QUERIES:
        url = config.GOOGLE_NEWS_URL.format(q=urllib.parse.quote_plus(q))
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            fallas.append(str(exc)[:120])
            continue
        for a in _desde_feed("Google News", r.content):
            a["tags"].append(f"query:{q}")
            articulos.append(a)
    if fallas and not articulos:
        raise RuntimeError(f"todas las búsquedas fallaron: {fallas[0]}")
    return articulos


def _descubre_articulo(url: str) -> dict | None:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    titulo = (soup.find("meta", property="og:title") or {}).get("content") or (soup.title.string if soup.title else "")
    desc = (soup.find("meta", attrs={"name": "description"}) or {}).get("content", "")
    mencionados = [a.get_text(strip=True) for a in soup.select("a[href^='https://www.descubre.vc/'], a[href^='/']")
                   if a.get("href", "").count("/") <= 3 and len(a.get_text(strip=True)) > 1][:12]
    m = re.search(r"(\d{4}-\d{2}-\d{2})$", url)
    fecha = f"{m.group(1)}T12:00:00+00:00" if m else datetime.now(timezone.utc).isoformat()
    return {
        "fuente": "Descubre.vc",
        "url": url,
        "titulo": html.unescape(titulo or "").strip(),
        "texto": _limpiar_html(desc),
        "fecha": fecha,
        "tags": [f"mencionado:{x}" for x in mencionados],
    }


def descubre() -> list[dict]:
    urls: list[str] = []
    for pagina in range(config.DESCUBRE_PAGINAS):
        r = requests.get(f"https://www.descubre.vc/noticias/{pagina}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for href in re.findall(r'href="(/noticia/[^"#?]+)"', r.text):
            completa = "https://www.descubre.vc" + href
            if completa not in urls:
                urls.append(completa)
    articulos = []
    for url in urls:
        try:
            a = _descubre_articulo(url)
            if a:
                articulos.append(a)
        except requests.RequestException:
            continue
    return articulos


def recolectar() -> tuple[list[dict], dict]:
    """Corre todas las fuentes. Devuelve (artículos, estado_por_fuente)."""
    todos: list[dict] = []
    estado: dict[str, dict] = {}

    tareas = [(n, (lambda n=n, u=u: rss(n, u))) for n, u in config.FUENTES_RSS.items()]
    tareas += [("Google News", google_news), ("Descubre.vc", descubre)]

    for nombre, fn in tareas:
        try:
            arts = fn()
            todos.extend(arts)
            estado[nombre] = {"ok": True, "articulos": len(arts)}
        except Exception as exc:  # una fuente caída no tumba la corrida
            estado[nombre] = {"ok": False, "articulos": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return todos, estado
