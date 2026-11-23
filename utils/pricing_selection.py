# -*- coding: utf-8 -*-
"""Valores del acuerdo y del origen del precio, comunes a Ventas y al Punto de Venta.

``PRICE_ORIGINS`` describe solo de dónde sale el precio unitario. El acuerdo, el
descuento manual y la promoción viven en ``PRICING_RULE_TYPES``.
"""

PRICING_RULE_TYPES = [
    ('none', 'Sin acuerdo'),
    ('fixed_price', 'Precio Fijo'),
    ('product', 'Descuento por Producto'),
    ('line', 'Descuento por Línea'),
    ('scheme', 'Descuento por Esquema'),
    ('global', 'Descuento Global'),
    ('manual', 'Descuento Manual'),
    ('promo', 'Promoción'),
]

PRICE_ORIGINS = [
    ('list', 'Precio de venta'),
    ('pricelist', 'Tarifa'),
    ('fixed_price', 'Precio fijo de acuerdo'),
    ('manual', 'Manual'),
    ('combo', 'Parte de combo'),
    ('tint', 'Entintado'),
]
