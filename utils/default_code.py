# -*- coding: utf-8 -*-
"""Normalización de la referencia interna (``default_code``) de productos."""

#: Marca de contexto con la que los canales de captura del usuario piden
#: exigir la referencia. ``create``/``write`` la retiran antes de llamar a
#: ``super()`` para que los registros que el core crea por dentro (p. ej. las
#: variantes de una plantilla nueva) no la hereden.
REQUIRE_DEFAULT_CODE = 'entintados_require_default_code'


def clean_default_code(vals):
    """Quita los espacios de los extremos y reduce las rachas internas a uno
    solo; una referencia que queda vacía se guarda como ``False``.

    Modifica ``vals`` en sitio y solo actúa si ``default_code`` viene como
    texto, para no tocar escrituras que no lo incluyen.
    """
    code = vals.get('default_code')
    if isinstance(code, str):
        vals['default_code'] = ' '.join(code.split()) or False
    return vals
