# -*- coding: utf-8 -*-
"""Texto del badge «Origen Acuerdo» de las líneas de venta.

Todos los orígenes (acuerdos, descuento manual, promociones y regalos) se
muestran como ``Nombre · 30%``: el color del badge ya indica el tipo, así que
el texto solo lleva lo que lo distingue.
"""

#: Separador entre el nombre y el porcentaje (o el término fijo).
BADGE_SEPARATOR = ' · '

#: Marca del producto de combo que copia el badge de su paquete.
COMBO_ORIGIN_PREFIX = '↳ '

FIXED_PRICE_LABEL = 'Precio fijo'

#: Decimales de la precisión ``Discount``.
DISCOUNT_DIGITS = 2


def format_discount_number(discount):
    """Número del porcentaje sin ceros sobrantes: ``30.0`` → ``'30'``, ``12.5`` → ``'12.5'``."""
    return f"{float(discount):.{DISCOUNT_DIGITS}f}".rstrip('0').rstrip('.')


def format_discount_percent(discount):
    """Porcentaje sin ceros sobrantes: ``30.0`` → ``'30%'``, ``12.5`` → ``'12.5%'``."""
    return f"{format_discount_number(discount)}%"


def format_origin_badge(name, discount=None):
    """``name · %``; ``discount`` puede ser un número o un término fijo como ``'Regalo'``.

    Sin ``discount`` devuelve solo el nombre; sin nombre, solo el segundo término.
    """
    name = (name or '').strip()
    if discount is None or discount is False:
        return name
    term = discount if isinstance(discount, str) else format_discount_percent(discount)
    return BADGE_SEPARATOR.join(filter(None, [name, term]))
