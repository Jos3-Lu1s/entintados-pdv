"""Perfil de aprobación limitada; conserva los permisos del administrador nativo."""
from odoo import api, models, _
from odoo.exceptions import AccessError
from lxml import etree


def assigned_only(record):
    if record.env.su:
        return False
    group = record.env.ref('entintados_pdv.group_approval_assigned_only', raise_if_not_found=False)
    return bool(group and record.env.user.has_group('entintados_pdv.group_approval_assigned_only')
                and not record.env.user.has_group('approvals.group_approval_manager'))


class ApprovalAssignedAccess(models.Model):
    _inherit = 'approval.request'

    @api.model
    def get_view(self, view_id=None, view_type='form', **options):
        result = super().get_view(view_id=view_id, view_type=view_type, **options)
        if assigned_only(self) and view_type in ('form', 'list', 'kanban'):
            result = dict(result)
            arch = etree.fromstring(result['arch'])
            for operation in ('create', 'edit', 'delete'):
                arch.set(operation, '0')
            for button in arch.xpath(".//button[@type='object']"):
                if button.get('name') in {'action_confirm', 'action_refuse', 'action_cancel',
                                          'action_draft', 'action_withdraw', 'action_reject_demo'}:
                    button.set('invisible', '1')
                elif button.get('name') == 'action_approve':
                    condition = button.get('invisible', 'False')
                    button.set('invisible', "(%s) or user_status != 'pending'" % condition)
            result['arch'] = etree.tostring(arch, encoding='unicode')
        return result

    def _check_assigned_approval_user(self):
        if assigned_only(self):
            for request in self:
                if self.env.user not in request.sudo().approver_ids.user_id:
                    raise AccessError(_('Solo puedes aprobar las solicitudes que tienes asignadas. Las demás son de solo lectura.'))

    @api.model_create_multi
    def create(self, vals_list):
        if assigned_only(self):
            raise AccessError(_('Tu perfil permite consultar y aprobar solicitudes asignadas, pero no crear solicitudes.'))
        return super().create(vals_list)

    def write(self, vals):
        if assigned_only(self):
            self._check_assigned_approval_user()
            # Elegir auditores forma parte del flujo de aprobación de demos.
            if any(name != 'material_auditor_ids' and not name.startswith('message_') for name in vals):
                raise AccessError(_('Tu perfil solo permite aprobar y asignar auditores; no editar los datos de la solicitud.'))
        return super().write(vals)

    def unlink(self):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite eliminar solicitudes.'))
        return super().unlink()

    def action_approve(self, approver=None):
        self._check_assigned_approval_user()
        if assigned_only(self) and approver:
            rows = approver if isinstance(approver, models.BaseModel) else self.env['approval.approver'].browse(approver)
            if any(row.user_id != self.env.user or row.request_id not in self for row in rows):
                raise AccessError(_('Solo puedes registrar tu propia aprobación.'))
        return super().action_approve(approver=approver)

    def action_refuse(self, approver=None):
        if assigned_only(self):
            raise AccessError(_('Este perfil solo permite aprobar solicitudes asignadas.'))
        return super().action_refuse(approver=approver)

    def action_confirm(self):
        if assigned_only(self):
            raise AccessError(_('Este perfil no permite enviar solicitudes a aprobación.'))
        return super().action_confirm()

    def action_draft(self):
        if assigned_only(self):
            raise AccessError(_('Este perfil no permite regresar solicitudes a borrador.'))
        return super().action_draft()

    def action_withdraw(self, approver=None):
        if assigned_only(self):
            raise AccessError(_('Este perfil no permite retirar aprobaciones.'))
        return super().action_withdraw(approver=approver)

    def _action_force_approval(self):
        if assigned_only(self):
            raise AccessError(_('Este perfil no permite forzar aprobaciones.'))
        return super()._action_force_approval()

    def action_cancel(self):
        if assigned_only(self):
            raise AccessError(_('Este perfil no permite cancelar solicitudes.'))
        return super().action_cancel()

    def action_reject_demo(self):
        if assigned_only(self):
            raise AccessError(_('Este perfil solo permite aprobar solicitudes asignadas.'))
        return super().action_reject_demo()


class ApproverAssignedAccess(models.Model):
    _inherit = 'approval.approver'

    def _check_assigned_actor(self):
        if assigned_only(self):
            for row in self:
                row.request_id._check_assigned_approval_user()
                if row.user_id != self.env.user:
                    raise AccessError(_('No puedes modificar la aprobación de otro usuario.'))
                if row.status != 'pending':
                    raise AccessError(_('Solo puedes aprobar una solicitud pendiente asignada a tu usuario.'))

    def action_approve(self):
        self._check_assigned_actor()
        return super().action_approve()

    @api.model_create_multi
    def create(self, vals_list):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite agregar aprobadores.'))
        return super().create(vals_list)

    def write(self, vals):
        if assigned_only(self):
            self._check_assigned_actor()
            if set(vals) - {'status'} or vals.get('status') != 'approved':
                raise AccessError(_('Solo puedes registrar tu propia aprobación.'))
        return super().write(vals)

    def unlink(self):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite eliminar aprobadores.'))
        return super().unlink()


class ApprovalProductAssignedAccess(models.Model):
    _inherit = 'approval.product.line'

    @api.model_create_multi
    def create(self, vals_list):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite modificar los productos de una solicitud.'))
        return super().create(vals_list)

    def write(self, vals):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite modificar los productos de una solicitud.'))
        return super().write(vals)

    def unlink(self):
        if assigned_only(self):
            raise AccessError(_('Tu perfil no permite modificar los productos de una solicitud.'))
        return super().unlink()
