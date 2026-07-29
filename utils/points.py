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

# Acepta "9Y24", "9Y 24", "9 Y 24", "2Y", "456", "456 Pts.", "9 onzas 24 pts"
_MIXED_RE = re.compile(
    r"^\s*(?:(?P<ounces>\d+)\s*(?:%s|onzas?|oz)\s*)?"
    r"(?:(?P<points>\d+)\s*(?:pts?\.?|puntos?)?)?\s*$" % OUNCE_SYMBOL,
    re.IGNORECASE,
)


def split_points(points):
    """Descompone un total de puntos en ``(onzas, puntos restantes)``.

    >>> split_points(456)
    (9, 24)
    >>> split_points(96)
    (2, 0)
    >>> split_points(24)
    (0, 24)
    """
    total = int(points or 0)
    sign = -1 if total < 0 else 1
    total = abs(total)
    return sign * (total // POINTS_PER_OUNCE), sign * (total % POINTS_PER_OUNCE)


def format_points(points):
    """Formatea un total de puntos en la notación mixta de la operación.

    Sigue exactamente la convención de las tablas del fabricante:
    con onzas y resto ``9Y 24``; con onzas exactas ``2Y``; sin onzas
    ``24 Pts.``.

    >>> format_points(456)
    '9Y 24'
    >>> format_points(96)
    '2Y'
    >>> format_points(24)
    '24 Pts.'
    >>> format_points(0)
    '0 Pts.'
    """
    total = int(points or 0)
    if total < 0:
        return "-%s" % format_points(-total)
    ounces, rest = split_points(total)
    if ounces and rest:
        return "%d%s %d" % (ounces, OUNCE_SYMBOL, rest)
    if ounces:
        return "%d%s" % (ounces, OUNCE_SYMBOL)
    return "%d Pts." % rest


def format_points_long(points):
    """Variante legible para etiquetas y reportes impresos.

    >>> format_points_long(456)
    '9 Onzas 24 Pts. (456 Pts.)'
    >>> format_points_long(24)
    '24 Pts.'
    """
    total = int(points or 0)
    ounces, rest = split_points(abs(total))
    if not ounces:
        return "%d Pts." % total
    parts = ["%d %s" % (ounces, "Onza" if ounces == 1 else "Onzas")]
    if rest:
        parts.append("%d Pts." % rest)
    return "%s (%d Pts.)" % (" ".join(parts), total)


def to_points(ounces=0, points=0):
    """Convierte una cantidad en notación mixta a puntos totales.

    >>> to_points(9, 24)
    456
    >>> to_points(ounces=2)
    96
    """
    return int(ounces or 0) * POINTS_PER_OUNCE + int(points or 0)


def parse_points(value):
    """Interpreta texto en notación mixta y devuelve puntos totales.

    Acepta ``"9Y 24"``, ``"9Y24"``, ``"2Y"``, ``"456"``, ``"456 Pts."``
    y ``"9 onzas 24 pts"``. Devuelve ``None`` si no puede interpretarlo,
    de forma que el llamador decida cómo reportar el error en lugar de
    recibir un cero silencioso.

    >>> parse_points("9Y 24")
    456
    >>> parse_points("2Y")
    96
    >>> parse_points("456")
    456
    >>> parse_points("no es un numero") is None
    True
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _MIXED_RE.match(str(value))
    if not match:
        return None
    ounces, points = match.group("ounces"), match.group("points")
    if ounces is None and points is None:
        return None
    return to_points(ounces or 0, points or 0)
