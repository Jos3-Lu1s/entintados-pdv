import math
import re

from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, AccessError


DEPARTMENTS = (
    'hr.dep_administration',
    'entintados_pdv.hr_department_creditos',
    'entintados_pdv.hr_department_tesoreria',
)


class ApprovalCategoryCredit(models.Model):
    _inherit = 'approval.category'

    pos_credit_category = fields.Boolean(string='Crédito excedido PPD', copy=False)


class ApprovalRequestCredit(models.Model):
    _inherit = 'approval.request'

    pos_credit_approval_id = fields.Many2one('pos.credit.approval', readonly=True, copy=False, ondelete='restrict')
    pos_credit_total = fields.Monetary(related='pos_credit_approval_id.total_amount', currency_field='pos_credit_currency_id')
    pos_credit_currency_id = fields.Many2one(related='pos_credit_approval_id.currency_id')
    pos_risk_currency_id = fields.Many2one(related='pos_credit_approval_id.risk_currency_id')
    pos_credit_limit = fields.Monetary(related='pos_credit_approval_id.credit_limit', currency_field='pos_risk_currency_id')
    pos_current_credit = fields.Monetary(
        related='pos_credit_approval_id.current_credit', currency_field='pos_risk_currency_id', string='Crédito a deber')
    pos_projected_risk = fields.Monetary(related='pos_credit_approval_id.projected_risk', currency_field='pos_risk_currency_id')
    pos_credit_state = fields.Selection(related='pos_credit_approval_id.state')
    pos_credit_branch_id = fields.Many2one(
        related='pos_credit_approval_id.company_id', string='Sucursal', store=True)
    pos_credit_amount = fields.Monetary(
        related='pos_credit_approval_id.credit_amount', currency_field='pos_credit_currency_id',
        string='Monto a autorizar', store=True)
    pos_credit_order_id = fields.Many2one(related='pos_credit_approval_id.order_id', string='Orden del PdV')
    pos_credit_cancel_reason = fields.Char(related='pos_credit_approval_id.cancel_reason', string='Motivo de anulación')
    pos_credit_cancelled_by = fields.Many2one(related='pos_credit_approval_id.cancelled_by', string='Cancelada por')
    pos_credit_cancelled_on = fields.Datetime(related='pos_credit_approval_id.cancelled_on', string='Fecha de anulación')
    can_cancel_pos_credit = fields.Boolean(compute='_compute_can_cancel_pos_credit')

    @api.depends_context('uid')
    @api.depends('pos_credit_approval_id.state', 'pos_credit_approval_id.used_order_id',
                 'request_owner_id', 'pos_credit_approval_id.approver_user_ids', 'pos_credit_approval_id.order_id.state')
    def _compute_can_cancel_pos_credit(self):
        for request in self:
            ticket = request.sudo().pos_credit_approval_id
            request.can_cancel_pos_credit = bool(
                ticket and ticket.state in ('pending', 'approved') and not ticket.used_order_id
                and (not ticket.order_id or ticket.order_id.state == 'draft')
                and (self.env.user == request.request_owner_id or
                     (self.env.user in ticket.approver_user_ids and
                      self.env.user in ticket._eligible_users(ticket.company_id)))
            )

    def action_cancel(self):
        credit = self.filtered('pos_credit_approval_id')
        credit.check_access('read')
        for request in credit:
            ticket = request.sudo().pos_credit_approval_id
            self.env.cr.execute('SELECT id FROM pos_credit_approval WHERE id = %s FOR UPDATE', [ticket.id])
            ticket.invalidate_recordset()
            if ticket.used_order_id or ticket.state == 'used':
                raise UserError(_('No puedes anular una autorización que ya se utilizó en una venta.'))
            if not self.env.su and not request.can_cancel_pos_credit:
                raise AccessError(_('Solo el solicitante o un responsable departamental asignado puede anular esta autorización.'))
            # Native cancellation changes every approver status, not its assignment.
            super(ApprovalRequestCredit, request.sudo()).action_cancel()
            if ticket.state != 'cancelled':
                ticket.write({'state': 'void', 'cancelled_by': self.env.uid, 'cancelled_on': fields.Datetime.now(),
                              'cancel_reason': _('Autorización anulada manualmente.'),
                              'decided_by': ticket.decided_by.id or self.env.uid,
                              'decided_on': ticket.decided_on or fields.Datetime.now()})
            request.sudo()._cancel_activities()
        ordinary = self - credit
        return super(ApprovalRequestCredit, ordinary).action_cancel() if ordinary else True

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                category = self.env['approval.category'].browse(vals.get('category_id') or self.env.context.get('default_category_id'))
                if vals.get('pos_credit_approval_id') or category.pos_credit_category:
                    raise AccessError(_('Las solicitudes de crédito excedido se generan desde el PdV.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and (self.sudo().filtered('pos_credit_approval_id') or vals.get('pos_credit_approval_id')):
            raise AccessError(_('Los datos de esta solicitud están vinculados al ticket del PdV y no pueden editarse.'))
        return super().write(vals)

    def action_draft(self):
        if self.sudo().filtered('pos_credit_approval_id'):
            raise UserError(_('Solicita una nueva autorización desde el PdV.'))
        return super().action_draft()

    def _check_pos_credit_decision(self):
        self.check_access('read')
        for request in self.sudo().filtered('pos_credit_approval_id'):
            ticket = request.pos_credit_approval_id.with_user(self.env.user)
            if not ticket.can_decide:
                raise AccessError(_('Solo los responsables departamentales asignados pueden decidir esta autorización pendiente.'))

    def action_approve(self, approver=None):
        self._check_pos_credit_decision()
        return super().action_approve(approver)

    def action_refuse(self, approver=None):
        self._check_pos_credit_decision()
        return super().action_refuse(approver)


class ApprovalApproverCredit(models.Model):
    _inherit = 'approval.approver'

    def write(self, vals):
        linked = self.sudo().filtered(lambda line: line.request_id.pos_credit_approval_id)
        if linked and not self.env.su:
            if set(vals) != {'status'} or vals['status'] not in ('approved', 'refused'):
                raise AccessError(_('Los responsables de crédito se asignan desde los departamentos autorizadores.'))
            for line in linked:
                ticket = line.request_id.pos_credit_approval_id
                self.env.cr.execute('SELECT id FROM pos_credit_approval WHERE id = %s FOR UPDATE', [ticket.id])
                ticket.invalidate_recordset()
                if line.user_id != self.env.user or not ticket.with_user(self.env.user).can_decide:
                    raise AccessError(_('Solo un responsable vigente de Administración, Créditos o Tesorería puede decidir una solicitud pendiente.'))
        result = super().write(vals)
        if linked and not self.env.su:
            for ticket in linked.request_id.pos_credit_approval_id:
                ticket.sudo().write({'state': vals['status'], 'decided_by': self.env.uid,
                                     'decided_on': fields.Datetime.now()})
        return result

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if self.env['approval.request'].browse(vals.get('request_id')).sudo().pos_credit_approval_id:
                    raise AccessError(_('Los responsables de crédito se asignan desde los departamentos autorizadores.'))
        return super().create(vals_list)

    def unlink(self):
        if not self.env.su and self.sudo().request_id.pos_credit_approval_id:
            raise AccessError(_('No puedes eliminar los responsables de una autorización de crédito.'))
        return super().unlink()


class PosCreditApproval(models.Model):
    _name = 'pos.credit.approval'
    _description = 'Autorización de crédito excedido del PdV'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, readonly=True)
    config_id = fields.Many2one('pos.config', required=True, readonly=True, ondelete='restrict')
    company_id = fields.Many2one(related='config_id.company_id', store=True)
    currency_id = fields.Many2one(related='config_id.currency_id')
    partner_id = fields.Many2one('res.partner', required=True, readonly=True, ondelete='restrict')
    order_uuid = fields.Char(required=True, readonly=True, index=True)
    order_id = fields.Many2one('pos.order', string='Orden del PdV', readonly=True, copy=False, index=True, ondelete='restrict')
    cancelled_by = fields.Many2one('res.users', string='Cancelada por', readonly=True)
    cancelled_on = fields.Datetime(string='Fecha de anulación', readonly=True)
    cancel_reason = fields.Char(string='Motivo de anulación', readonly=True)
    previous_credit_amount = fields.Monetary(string='Monto solicitado anterior', readonly=True)
    revised_on = fields.Datetime(string='Última actualización del monto', readonly=True)
    revised_by = fields.Many2one('res.users', string='Monto actualizado por', readonly=True)
    total_amount = fields.Monetary(string='Total del ticket', required=True, readonly=True)
    credit_amount = fields.Monetary(string='Saldo por financiar', required=True, readonly=True)
    applied_amount = fields.Monetary(string='Anticipos aplicados', readonly=True)
    risk_currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    credit_limit = fields.Monetary(currency_field='risk_currency_id', string='Límite de crédito', readonly=True)
    current_credit = fields.Monetary(
        currency_field='risk_currency_id', string='Crédito a deber', compute='_compute_current_credit', compute_sudo=True,
        help='Deuda actual del cliente en la compañía de esta solicitud. Se actualiza al consultar el formulario.')
    projected_risk = fields.Monetary(currency_field='risk_currency_id', string='Riesgo proyectado autorizado', readonly=True)
    requester_id = fields.Many2one('res.users', required=True, readonly=True)
    approver_user_ids = fields.Many2many('res.users', string='Responsables', readonly=True)
    decided_by = fields.Many2one('res.users', readonly=True, string='Decisión por')
    decided_on = fields.Datetime(readonly=True, string='Fecha de decisión')
    used_order_id = fields.Many2one('pos.order', readonly=True, string='Venta autorizada', ondelete='restrict')
    state = fields.Selection([
        ('pending', 'Pendiente'), ('approved', 'Autorizada'),
        ('refused', 'Rechazada'), ('used', 'Utilizada'), ('cancelled', 'Sustituida'), ('void', 'Anulada'),
    ], default='pending', readonly=True, tracking=True, required=True)
    can_decide = fields.Boolean(compute='_compute_can_decide')
    approval_request_id = fields.Many2one('approval.request', readonly=True, copy=False, ondelete='restrict')

    @api.depends('partner_id', 'company_id', 'risk_currency_id')
    def _compute_current_credit(self):
        for request in self:
            partner = request.partner_id.with_company(request.company_id).with_context(
                allowed_company_ids=request.company_id.ids)
            request.current_credit = partner.risk_currency_id._convert(
                partner._pos_credit_exposure(), request.risk_currency_id, request.company_id,
                fields.Date.context_today(request)) if partner else 0

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env.ref('entintados_pdv.seq_pos_credit_approval').sudo()
        return super().create([dict(vals, name=sequence.next_by_id()) for vals in vals_list])

    @api.model
    def _setup_native_approvals(self):
        """Initialize per-company categories and preserve existing ticket decisions."""
        for company in self.env['res.company'].sudo().search([]):
            self._native_category(company)
        for request in self.sudo().search([('approval_request_id', '=', False)]):
            request._create_native_request()
        sequence = self.env.ref('entintados_pdv.seq_pos_credit_approval').sudo()
        for request in self.sudo().search([], order='id'):
            if not request.order_id:
                order = self.env['pos.order'].sudo().search([
                    ('uuid', '=', request.order_uuid), ('config_id', '=', request.config_id.id),
                    ('partner_id.commercial_partner_id', '=', request.partner_id.id)], limit=1)
                if order:
                    request.order_id = order
            if not re.fullmatch(r'ACRED-\d{4,}', request.name or ''):
                request.name = sequence.next_by_id()
            if request.approval_request_id.name != request.name:
                request.approval_request_id.sudo().write({'name': request.name})
            if request.state == 'pending' and (not request.order_id or request.order_id.state == 'draft'):
                partner = request.partner_id.with_company(request.company_id)
                projected = request.config_id._credit_projection(
                    partner, request.credit_amount, request.applied_amount, order=request.order_id)
                projected = partner.risk_currency_id._convert(
                    projected, request.risk_currency_id, request.company_id, fields.Date.context_today(request))
                if request.risk_currency_id.compare_amounts(projected, request.projected_risk):
                    request.projected_risk = projected
                    request.approval_request_id.message_post(body=_(
                        'Riesgo pendiente recalculado: los anticipos disponibles se mantienen separados del crédito.'))
        self.env['pos.order'].sudo().search([
            ('id', 'in', self.sudo().search([('order_id', '!=', False)]).order_id.ids),
            ('state', '=', 'cancel')])._cancel_credit_approvals()

    @api.model
    def _native_category(self, company):
        Category = self.env['approval.category'].sudo()
        category = Category.search([('pos_credit_category', '=', True), ('company_id', '=', company.id)], limit=1)
        users = self._eligible_users(company)
        if not category:
            category = Category.create({
                'name': 'Crédito excedido de ventas PPD', 'company_id': company.id,
                'pos_credit_category': True, 'approval_minimum': 1,
                'has_amount': 'required', 'has_partner': 'required', 'has_reference': 'required',
                'description': 'Autorización de Administración, Créditos o Tesorería para ventas que exceden el crédito disponible.',
            })
        if (set(category.approver_ids.user_id.ids) != set(users.ids) or category.approval_minimum != 1
                or category.approver_sequence or category.manager_approval or category.approver_ids.filtered('required')):
            category.write({'approval_minimum': 1, 'approver_sequence': False, 'manager_approval': False,
                            'approver_ids': [Command.clear()] + [Command.create({'user_id': user.id, 'required': False}) for user in users]})
        return category

    def _create_native_request(self):
        self.ensure_one()
        if self.approval_request_id:
            return self.approval_request_id
        category = self._native_category(self.company_id)
        native = self.env['approval.request'].sudo().create({
            'name': self.name, 'category_id': category.id, 'request_owner_id': self.requester_id.id,
            'partner_id': self.partner_id.id, 'amount': self.credit_amount,
            'reference': self.order_id.pos_reference or self.order_uuid,
            'pos_credit_approval_id': self.id,
            'approver_ids': [Command.create({'user_id': user.id, 'required': False}) for user in self.approver_user_ids],
        })
        self.sudo().approval_request_id = native
        if self.state == 'pending':
            native.action_confirm()
        else:
            status = {'approved': 'approved', 'used': 'approved', 'refused': 'refused', 'cancelled': 'cancel', 'void': 'cancel'}[self.state]
            native.approver_ids.sudo().write({'status': status})
        self.sudo().activity_ids.action_feedback(feedback=_('La autorización se gestiona ahora en Aprobaciones.'))
        return native

    def action_open_approval(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': 'approval.request',
                'res_id': self.approval_request_id.id, 'view_mode': 'form', 'target': 'current'}

    @api.model
    def _eligible_users(self, company):
        departments = self.env['hr.department']
        for xmlid in DEPARTMENTS:
            departments |= self.env.ref(xmlid, raise_if_not_found=False) or departments.browse()
        employees = self.env['hr.employee'].sudo().search([
            ('department_id', 'in', departments.ids), ('company_id', '=', company.id),
            ('user_id', '!=', False), ('active', '=', True),
        ])
        return employees.user_id.filtered(lambda user: user.active and not user.share and company in user.company_ids)

    @api.depends_context('uid')
    @api.depends('state', 'company_id', 'approver_user_ids', 'order_id.state')
    def _compute_can_decide(self):
        for request in self:
            request.can_decide = (
                request.state == 'pending'
                and (not request.sudo().order_id or request.sudo().order_id.state == 'draft')
                and self.env.user in request.sudo().approver_user_ids
                and self.env.user in request._eligible_users(request.company_id)
            )

    def _decide(self, state):
        self.check_access('read')
        for request in self:
            native = request.approval_request_id
            if state == 'approved':
                native.action_approve()
            else:
                native.action_refuse()
        return True

    def action_approve(self):
        return self._decide('approved')

    def action_refuse(self):
        return self._decide('refused')

    def _matches(self, total, credit, applied):
        self.ensure_one()
        return all(self.currency_id.compare_amounts(old, new) == 0 for old, new in (
            (self.total_amount, total), (self.credit_amount, credit), (self.applied_amount, applied),
        ))

    def _pos_payload(self):
        self.ensure_one()
        return {'id': self.id, 'name': self.name, 'state': self.state, 'credit_amount': self.credit_amount,
                'total_amount': self.total_amount, 'applied_amount': self.applied_amount,
                'previous_credit_amount': self.previous_credit_amount, 'revised': bool(self.revised_on)}

    def _revise_pending_request(self, total, credit, applied, partner, projected, actor):
        """Keep the folio; discard all prior decisions before requesting approval again."""
        self.ensure_one()
        users = self._eligible_users(self.company_id)
        if not users:
            raise UserError(_('No hay responsables departamentales activos para revisar la solicitud actualizada.'))
        old = self.credit_amount
        prior_state = dict(self._fields['state'].selection).get(self.state, self.state)
        prior_user = self.decided_by.display_name or _('Sin decisión')
        amount_changed = not self._matches(total, credit, applied)
        values = {
            'state': 'pending', 'total_amount': total, 'credit_amount': credit, 'applied_amount': applied,
            'risk_currency_id': partner.risk_currency_id.id, 'credit_limit': partner.credit_limit,
            'projected_risk': projected, 'decided_by': False, 'decided_on': False,
            'approver_user_ids': [Command.set(users.ids)],
        }
        if amount_changed:
            values.update(previous_credit_amount=old, revised_by=actor.id, revised_on=fields.Datetime.now())
        self.write(values)
        native = self.approval_request_id.sudo()
        native._cancel_activities()
        native.write({'amount': credit, 'approver_ids': [Command.clear()] + [
            Command.create({'user_id': user.id, 'required': False}) for user in users]})
        native.action_confirm()
        native.message_post(body=_(
            'Solicitud actualizada por %(user)s. Monto anterior: %(old).2f; monto nuevo: %(new).2f. '
            'Decisión anterior: %(prior_state)s; responsable: %(prior_user)s. '
            'Las decisiones anteriores quedan sin efecto y se requiere una nueva autorización.',
            user=actor.display_name, old=old, new=credit, prior_state=prior_state,
            prior_user=prior_user), author_id=actor.partner_id.id)


class PosConfigCreditApproval(models.Model):
    _inherit = 'pos.config'

    def _credit_projection(self, partner, credit, applied, order=None):
        self.ensure_one()
        partner = partner.sudo().with_company(self.company_id).with_context(allowed_company_ids=self.company_id.ids)
        date = fields.Date.context_today(self)
        extra = self.currency_id._convert(credit, partner.risk_currency_id, self.company_id, date)
        exposure = partner._pos_credit_exposure(exclude_order=order)
        # Explicitly applied advances already reduce the ticket and its financed remainder.
        return max(exposure + extra, 0)

    def request_credit_approval(self, partner_id, order_uuid, total, credit, applied=0, order_id=None, new_attempt=False):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('point_of_sale.group_pos_user') or self.company_id not in self.env.companies:
            raise AccessError(_('La solicitud requiere acceso al Punto de Venta y a su compañía.'))
        if not isinstance(order_uuid, str) or not order_uuid or len(order_uuid) > 128:
            raise UserError(_('El ticket no tiene un identificador válido.'))
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (total, credit, applied)):
            raise UserError(_('Los importes de la solicitud no son válidos.'))
        if total <= 0 or credit <= 0 or credit > total or applied < 0:
            raise UserError(_('Revisa el total, el saldo pendiente y los anticipos del ticket.'))
        partner = self.env['res.partner'].browse(partner_id).exists()
        partner.check_access('read')
        partner = partner.commercial_partner_id.sudo().with_company(self.company_id).with_context(
            allowed_company_ids=self.company_id.ids)
        if not partner or not partner.is_credit:
            raise UserError(_('El cliente necesita la condición Crédito habilitada.'))
        order = self.env['pos.order'].browse(order_id).exists() if order_id else self.env['pos.order'].search([
            ('uuid', '=', order_uuid), ('config_id', '=', self.id)], limit=1)
        order.check_access('read')
        if (not order or order.config_id != self or order.uuid != order_uuid or order.state != 'draft'
                or order.partner_id.commercial_partner_id != partner):
            raise UserError(_('Guarda el ticket en borrador antes de solicitar autorización. La solicitud debe corresponder a esa orden y cliente.'))
        self.env.cr.execute('SELECT id FROM pos_order WHERE id = %s FOR UPDATE', [order.id])
        order.invalidate_recordset()
        if order.state != 'draft' or order.partner_id.commercial_partner_id != partner:
            raise UserError(_('La orden cambió o fue cancelada. Recupera su estado antes de solicitar crédito.'))
        if self.currency_id.compare_amounts(order.amount_total, total):
            raise UserError(_('El total de la solicitud no coincide con el ticket guardado. Sincroniza la orden.'))
        self.env.cr.execute('SELECT id FROM res_partner WHERE id = %s FOR UPDATE', [partner.id])
        partner.invalidate_recordset()
        projected = self._credit_projection(partner, credit, applied)
        Approval = self.env['pos.credit.approval'].sudo()
        # Adopt a pre-upgrade request only when its exact UUID identifies this saved ticket.
        legacy = Approval.search([
            ('config_id', '=', self.id), ('order_uuid', '=', order_uuid), ('order_id', '=', False),
            ('partner_id', '=', partner.id), ('state', 'in', ['pending', 'approved', 'refused']),
        ])
        if legacy:
            legacy.write({'order_id': order.id})
            legacy.approval_request_id.write({'reference': order.pos_reference or order.uuid})
        request = Approval.search([
            ('config_id', '=', self.id), ('order_id', '=', order.id),
            ('partner_id', '=', partner.id), ('state', 'in', ['pending', 'approved', 'refused']),
        ], limit=1)
        if request:
            self.env.cr.execute('SELECT id FROM pos_credit_approval WHERE id = %s FOR UPDATE', [request.id])
            request.invalidate_recordset()
            if request.state not in ('pending', 'approved', 'refused') or request.used_order_id:
                raise UserError(_('Esta autorización ya no puede actualizarse. Recupera el estado de la orden.'))
        previous_refusal = Approval.browse()
        if request and request.state == 'refused':
            if not new_attempt:
                return request._pos_payload()
            previous_refusal = request
            request = Approval.browse()
        if request and (not request._matches(total, credit, applied)
                        or request.risk_currency_id != partner.risk_currency_id
                        or partner.risk_currency_id.compare_amounts(partner.credit_limit, request.credit_limit)
                        or partner.risk_currency_id.compare_amounts(projected, request.projected_risk) > 0):
            request._revise_pending_request(total, credit, applied, partner, projected, self.env.user)
        if not request:
            users = Approval._eligible_users(self.company_id)
            if not users:
                raise UserError(_('No hay usuarios activos con empleado en Administración, Créditos o Tesorería para esta compañía.'))
            request = Approval.create({
                'config_id': self.id, 'order_uuid': order_uuid, 'order_id': order.id, 'partner_id': partner.id,
                'total_amount': total, 'credit_amount': credit, 'applied_amount': applied,
                'risk_currency_id': partner.risk_currency_id.id,
                'credit_limit': partner.credit_limit, 'projected_risk': projected,
                'requester_id': self.env.uid, 'approver_user_ids': [(6, 0, users.ids)],
            })
            request._create_native_request()
            if previous_refusal:
                request.approval_request_id.message_post(body=_(
                    'Nuevo intento de crédito tras el rechazo de la solicitud %(previous)s.', previous=previous_refusal.name))
                previous_refusal.approval_request_id.message_post(body=_(
                    'Se generó la nueva solicitud %(new)s. Este folio conserva su estado Rechazada.', new=request.name))
        return request._pos_payload()

    def get_order_credit_approval(self, order_id):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('point_of_sale.group_pos_user') or self.company_id not in self.env.companies:
            raise AccessError(_('Se requiere acceso al PdV y a su compañía.'))
        order = self.env['pos.order'].browse(order_id).exists()
        order.check_access('read')
        if not order or order.config_id != self:
            raise AccessError(_('La orden no pertenece a este Punto de Venta.'))
        request = self.env['pos.credit.approval'].sudo().search([
            ('order_id', '=', order.id), ('partner_id', '=', order.partner_id.commercial_partner_id.id),
        ], limit=1)
        return request._pos_payload() if request else False


class PosOrderCreditApproval(models.Model):
    _inherit = 'pos.order'

    credit_approval_ids = fields.One2many('pos.credit.approval', 'order_id', string='Autorizaciones de crédito')
    credit_waiting_state = fields.Selection(
        [('waiting', 'Por autorizar')], string='Estado', compute='_compute_credit_waiting_state', compute_sudo=True)

    @api.depends('state', 'partner_id', 'credit_approval_ids.state', 'credit_approval_ids.partner_id')
    def _compute_credit_waiting_state(self):
        for order in self:
            requests = order.credit_approval_ids.filtered(
                lambda request: request.partner_id == order.partner_id.commercial_partner_id)
            latest = requests.sorted('id', reverse=True)[:1]
            order.credit_waiting_state = (
                'waiting' if order.state == 'draft' and latest.state == 'pending' else False)

    def _cancel_credit_approvals(self):
        """Keep the order and its credit history; only unused approvals are revoked."""
        requests = self.env['pos.credit.approval'].sudo().search([('order_id', 'in', self.ids)])
        for request in requests.sorted('id'):
            self.env.cr.execute('SELECT id FROM pos_credit_approval WHERE id = %s FOR UPDATE', [request.id])
            request.invalidate_recordset()
            if request.state in ('pending', 'approved', 'refused') and not request.used_order_id:
                request.approval_request_id.sudo().action_cancel()
            if not request.cancelled_on or request.cancel_reason != _('Orden del PdV cancelada.'):
                request.write({'cancel_reason': _('Orden del PdV cancelada.'),
                               'cancelled_by': self.env.uid, 'cancelled_on': fields.Datetime.now()})
                request.approval_request_id.message_post(body=_(
                    'Orden del PdV cancelada. La autorización no puede transferirse ni reutilizarse.'))

    def write(self, vals):
        result = super().write(vals)
        if vals.get('state') == 'cancel':
            self._cancel_credit_approvals()
        return result

    def unlink(self):
        self.check_access('unlink')
        linked_ids = self.env['pos.credit.approval'].sudo().search([('order_id', 'in', self.ids)]).order_id.ids
        retained = self.filtered(lambda order: order.id in linked_ids)
        if retained.filtered(lambda order: order.state not in ('draft', 'cancel')):
            raise UserError(_('No puedes eliminar una venta confirmada con historial de autorización.'))
        if retained:
            retained.write({'state': 'cancel'})
        return super(PosOrderCreditApproval, self - retained).unlink() if self - retained else True

    def _require_credit_approval(self, partner, credit, applied):
        self.ensure_one()
        Approval = self.env['pos.credit.approval'].sudo()
        request = Approval.search([
            ('config_id', '=', self.config_id.id), ('order_id', '=', self.id),
            ('partner_id', '=', partner.id), ('state', 'in', ['pending', 'approved', 'refused', 'used']),
        ], limit=1)
        if not request or request.state not in ('approved', 'used'):
            raise UserError(_('El crédito disponible fue excedido. Solicita autorización desde Cuenta de cliente y espera la aprobación.'))
        self.env.cr.execute('SELECT id FROM pos_credit_approval WHERE id = %s FOR UPDATE', [request.id])
        request.invalidate_recordset()
        projected = self.config_id._credit_projection(partner, credit, applied, order=self)
        if (request.state not in ('approved', 'used')
                or self.state == 'cancel'
                or request.approval_request_id.request_status != 'approved'
                or not request._matches(self.amount_total, credit, applied)
                or request.risk_currency_id != partner.risk_currency_id
                or partner.risk_currency_id.compare_amounts(partner.credit_limit, request.credit_limit)
                or request.risk_currency_id.compare_amounts(projected, request.projected_risk) > 0
                or (request.used_order_id and request.used_order_id != self)):
            raise UserError(_('La autorización no cubre esta venta o el riesgo actual. Solicita una nueva aprobación.'))
        request.write({'state': 'used', 'used_order_id': self.id})
