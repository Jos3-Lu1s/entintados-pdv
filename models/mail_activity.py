# -*- coding: utf-8 -*-

from odoo import api, models


class MailActivity(models.Model):
    """Amplía el acceso de lectura para consulta y análisis global de actividades."""
    _inherit = 'mail.activity'

    def _is_activity_analyst(self):
        """Verifica si el usuario tiene permisos de administrador de actividades."""
        return self.env.user.has_group('entintados_pdv.group_activity_manager')

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *, bypass_access=False, **kwargs):
        # Omite restricciones de acceso para administradores de actividades.
        if not bypass_access and self._is_activity_analyst():
            bypass_access = True
        return super()._search(domain, offset, limit, order, bypass_access=bypass_access, **kwargs)

    def _check_access(self, operation):
        # Permite acceso de lectura global al administrador de actividades.
        if operation == 'read' and self._is_activity_analyst():
            return None
        return super()._check_access(operation)
