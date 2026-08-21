# -*- coding: utf-8 -*-
"""Conversión y formato de puntos de colorante.

El punto es la unidad atómica de dispensado de colorante. Una onza
equivale exactamente a 48 puntos, y la operación razona en notación
mixta: ``9Y 24`` significa 9 onzas más 24 puntos, es decir 456 puntos.

Estas funciones son Python puro, sin dependencias de Odoo, para poder
probarlas de forma aislada y reutilizarlas tanto en el backend como en
la generación de etiquetas.
"""

import re

#: Puntos que contiene una onza de colorante. Es un estándar físico del
#: oficio (un punto es 1/48 de onza), no un parámetro de configuración.
POINTS_PER_OUNCE = 48

#: Símbolo con el que la operación denota las onzas en la notación mixta.
OUNCE_SYMBOL = "Y"

# Acepta "9Y24", "9Y 24", "9Y 24.5", "9 Y 24", "2Y", "456", "456.5", "456 Pts.", "0.5 Pts.", "9 onzas 24 pts"
_MIXED_RE = re.compile(
    r"^\s*(?:(?P<ounces>\d+(?:\.\d+)?)\s*(?:%s|onzas?|oz)\s*)?"
    r"(?:(?P<points>\d+(?:\.\d+)?)\s*(?:pts?\.?|puntos?)?)?\s*$" % OUNCE_SYMBOL,
    re.IGNORECASE,
)


def _fmt_num(val):
    """Formatea un número omitiendo decimales si es entero o mostrando decimales limpios."""
    fval = float(val or 0)
    if fval.is_integer():
        return str(int(fval))
    return f"{round(fval, 4):.4f}".rstrip('0').rstrip('.')


def split_points(points):
    """Descompone un total de puntos en ``(onzas, puntos restantes)``.

    >>> split_points(456)
    (9, 24)
    >>> split_points(96)
    (2, 0)
    >>> split_points(24.5)
    (0, 24.5)
    """
    total = float(points or 0)
    sign = -1 if total < 0 else 1
    total = abs(total)
    ounces = int(total // POINTS_PER_OUNCE)
    rest = round(total % POINTS_PER_OUNCE, 4)
    if rest.is_integer():
        rest = int(rest)
    return sign * ounces, sign * rest


def format_points(points):
    """Formatea un total de puntos en la notación mixta de la operación.

    Sigue exactamente la convención de las tablas del fabricante:
    con onzas y resto ``9Y 24``; con onzas exactas ``2Y``; sin onzas
    ``24 Pts.`` o ``24.5 Pts.``.

    >>> format_points(456)
    '9Y 24'
    >>> format_points(456.5)
    '9Y 24.5'
    >>> format_points(96)
    '2Y'
    >>> format_points(24)
    '24 Pts.'
    >>> format_points(0)
    '0 Pts.'
    """
    total = float(points or 0)
    if total < 0:
        return "-%s" % format_points(-total)
    ounces, rest = split_points(total)
    if ounces and rest:
        return "%d%s %s" % (ounces, OUNCE_SYMBOL, _fmt_num(rest))
    if ounces:
        return "%d%s" % (ounces, OUNCE_SYMBOL)
    return "%s Pts." % _fmt_num(rest)


def format_points_long(points):
    """Variante legible para etiquetas y reportes impresos.

    >>> format_points_long(456)
    '9 Onzas 24 Pts. (456 Pts.)'
    >>> format_points_long(24.5)
    '24.5 Pts.'
    """
    total = float(points or 0)
    ounces, rest = split_points(abs(total))
    fmt_total = _fmt_num(total)
    if not ounces:
        return "%s Pts." % _fmt_num(total)
    parts = ["%d %s" % (ounces, "Onza" if ounces == 1 else "Onzas")]
    if rest:
        parts.append("%s Pts." % _fmt_num(rest))
    return "%s (%s Pts.)" % (" ".join(parts), fmt_total)


def to_points(ounces=0, points=0):
    """Convierte una cantidad en notación mixta a puntos totales.

    >>> to_points(9, 24)
    456
    >>> to_points(9, 24.5)
    456.5
    """
    res = float(ounces or 0) * POINTS_PER_OUNCE + float(points or 0)
    return int(res) if res.is_integer() else round(res, 4)


def parse_points(value):
    """Interpreta texto en notación mixta y devuelve puntos totales.

    Acepta ``"9Y 24"``, ``"9Y 24.5"``, ``"2Y"``, ``"456"``, ``"456.5 Pts."``
    y ``"9 onzas 24 pts"``. Devuelve ``None`` si no puede interpretarlo,
    de forma que el llamador decida cómo reportar el error en lugar de
    recibir un cero silencioso.

    >>> parse_points("9Y 24")
    456
    >>> parse_points("9Y 24.5")
    456.5
    >>> parse_points("no es un numero") is None
    True
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return int(val) if val.is_integer() else val
    match = _MIXED_RE.match(str(value).strip())
    if not match:
        return None
    ounces, points = match.group("ounces"), match.group("points")
    if ounces is None and points is None:
        return None
    return to_points(ounces or 0, points or 0)
