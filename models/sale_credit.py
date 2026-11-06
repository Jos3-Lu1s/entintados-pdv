import json

from odoo import api, Command, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_amount



class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sale_advance_enabled = fields.Boolean(related='company_id.advance_risk_enabled')

    def _get_invoice_grouping_keys(self):
        return super()._get_invoice_grouping_keys() + ['l10n_mx_edi_payment_policy']

    def action_sale_cash_advance(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_window', 'res_model': 'sale.cash.advance',
            'name': _('Recibir anticipo'), 'view_mode': 'form', 'target': 'new',
            'context': {'default_order_id': self.id},
        }


class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    sale_credit_category = fields.Boolean(copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get('sale_credit_category', self.env.context.get('default_sale_credit_category'))
                                   for vals in vals_list):
            raise AccessError(_('La categoría de crédito se configura desde Ventas.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and ('sale_credit_category' in vals or self.sudo().filtered('sale_credit_category')):
            raise AccessError(_('La categoría de crédito se configura desde Ventas.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.sudo().filtered('sale_credit_category'):
            raise AccessError(_('La categoría de crédito conserva el historial de Ventas.'))
        return super().unlink()



class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    sale_credit_move_id = fields.Many2one('account.move', string='Factura de venta',
                                          readonly=True, copy=False, ondelete='restrict', index=True)
    sale_credit_scope = fields.Char(readonly=True, copy=False)
    sale_credit_projected = fields.Float(readonly=True, copy=False)
    sale_credit_consumed = fields.Boolean(readonly=True, copy=False)
    credit_display_company_id = fields.Many2one('res.company', string='Sucursal', compute='_compute_credit_display')
    credit_display_currency_id = fields.Many2one('res.currency', compute='_compute_credit_display')
    credit_display_amount = fields.Monetary(string='Monto a autorizar', currency_field='credit_display_currency_id',
                                           compute='_compute_credit_display')

    @api.depends('pos_credit_branch_id', 'pos_credit_currency_id', 'pos_credit_amount',
                 'sale_credit_move_id.currency_id', 'amount', 'company_id')
    def _compute_credit_display(self):
        for request in self:
            invoice = request.sudo().sale_credit_move_id
            request.credit_display_company_id = request.company_id if invoice else request.pos_credit_branch_id
            request.credit_display_currency_id = invoice.currency_id or request.pos_credit_currency_id
            request.credit_display_amount = request.amount if invoice else request.pos_credit_amount

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                category = self.env['approval.category'].sudo().browse(
                    vals.get('category_id', self.env.context.get('default_category_id')))
                if category.sale_credit_category or vals.get('sale_credit_move_id', self.env.context.get('default_sale_credit_move_id')):
                    raise AccessError(_('Genera la autorización desde la factura de Ventas.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and (
            self.sudo().filtered('sale_credit_move_id') or vals.get('sale_credit_move_id')
            or self.env['approval.category'].sudo().browse(vals.get('category_id')).sale_credit_category
        ):
            raise AccessError(_('La autorización se modifica desde la factura de Ventas.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.sudo().filtered('sale_credit_move_id'):
            raise AccessError(_('No se puede eliminar el historial de autorización de Ventas.'))
        return super().unlink()

    def _lock_sale_credit_requests(self):
        if self:
            self.env.cr.execute('SELECT id FROM approval_request WHERE id IN %s ORDER BY id FOR NO KEY UPDATE',
                                [tuple(self.ids)])
            self.invalidate_recordset()

    def action_confirm(self):
        if not self.env.su and self.sudo().filtered('sale_credit_move_id'):
            raise AccessError(_('Envía la solicitud desde la factura de Ventas.'))
        return super().action_confirm()

    def action_cancel(self):
        if not self.env.su and self.sudo().filtered('sale_credit_move_id'):
            raise AccessError(_('La solicitud se conserva en el historial de la factura.'))
        return super().action_cancel()

    def action_draft(self):
        if not self.env.su and self.sudo().filtered('sale_credit_move_id'):
            raise AccessError(_('Solicita una nueva autorización desde la factura de Ventas.'))
        return super().action_draft()

    def action_approve(self, approver=None):
        pending = self.sudo().filtered(lambda r: r.sale_credit_move_id
                                      and r.request_status == 'pending' and not r.sale_credit_consumed)
        result = super().action_approve(approver)
        for request in pending.filtered(lambda r: r.request_status == 'approved'):
            invoice = request.sale_credit_move_id
            if invoice.state == 'draft' and invoice._is_sale_credit_invoice():
                # Reuse all posting checks, including the approved amount and current exposure.
                invoice.with_company(invoice.company_id).action_post()
        return result



class ApprovalApprover(models.Model):
    _inherit = 'approval.approver'

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(self.env['approval.request'].sudo().browse(
                vals.get('request_id', self.env.context.get('default_request_id'))).sale_credit_move_id for vals in vals_list):
            raise AccessError(_('Los aprobadores de crédito se asignan desde los departamentos autorizadores.'))
        return super().create(vals_list)

    def unlink(self):
        if not self.env.su and self.sudo().request_id.filtered('sale_credit_move_id'):
            raise AccessError(_('No puedes eliminar aprobadores de crédito de Ventas.'))
        return super().unlink()

    def write(self, vals):
        if not self.env.su:
            target = self.env['approval.request'].sudo().browse(vals.get('request_id'))
            if target.sale_credit_move_id:
                raise AccessError(_('No puedes modificar los aprobadores de crédito de Ventas.'))
            lines = self.sudo().filtered(lambda line: line.request_id.sale_credit_move_id)
            if lines:
                lines.request_id._lock_sale_credit_requests()
                lines.invalidate_recordset()
            for line in lines:
                request = line.request_id
                eligible = self.env['pos.credit.approval']._eligible_users(request.company_id)
                if (set(vals) != {'status'} or vals['status'] not in ('approved', 'refused')
                        or request.sale_credit_consumed or request.sale_credit_move_id.state != 'draft'
                        or request.request_status != 'pending' or line.status != 'pending'
                        or line.user_id != self.env.user or line.user_id not in eligible):
                    raise AccessError(_('Solo un responsable vigente y asignado puede decidir una solicitud pendiente.'))
        return super().write(vals)


class AccountMove(models.Model):
    _inherit = 'account.move'

    sale_credit_request_ids = fields.One2many('approval.request', 'sale_credit_move_id', readonly=True)
    sale_credit_needs_review = fields.Boolean(compute='_compute_sale_credit_needs_review')
    sale_credit_status = fields.Char(string='Autorización de crédito', compute='_compute_sale_credit_status')
    sale_credit_stage = fields.Selection([
        ('draft', 'Borrador'), ('credit_pending', 'Autorización pendiente'),
        ('posted', 'Registrada'), ('cancel', 'Cancelada'),
    ], string='Estado de factura', compute='_compute_sale_credit_stage')

    @api.depends('state', 'sale_credit_request_ids.request_status',
                 'sale_credit_request_ids.sale_credit_consumed')
    def _compute_sale_credit_stage(self):
        for move in self:
            latest = move.sudo().sale_credit_request_ids.sorted('id', reverse=True)[:1]
            move.sale_credit_stage = ('credit_pending' if move.state == 'draft'
                                     and latest.request_status == 'pending'
                                     and not latest.sale_credit_consumed else move.state)

    @api.depends('sale_credit_request_ids.name', 'sale_credit_request_ids.request_status',
                 'sale_credit_request_ids.sale_credit_consumed')
    def _compute_sale_credit_status(self):
        statuses = {'pending': _('Pendiente'), 'approved': _('Aprobada'), 'refused': _('Rechazada'),
                    'cancel': _('Anulada')}
        for move in self:
            request = move.sudo().sale_credit_request_ids.sorted('id', reverse=True)[:1]
            status = _('Utilizada') if request.sale_credit_consumed else statuses.get(request.request_status)
            move.sale_credit_status = '%s · %s' % (request.name, status) if status else False

    @api.depends('move_type', 'l10n_mx_edi_payment_policy', 'pos_order_ids', 'is_advance_invoice',
                 'invoice_line_ids.sale_line_ids.order_id.l10n_mx_edi_payment_policy')
    def _compute_sale_credit_needs_review(self):
        for move in self:
            move.sale_credit_needs_review = move._is_sale_credit_invoice()

    @api.depends('invoice_line_ids.sale_line_ids.order_id.l10n_mx_edi_payment_policy', 'is_advance_invoice', 'pos_order_ids')
    def _compute_l10n_mx_edi_payment_policy(self):
        super()._compute_l10n_mx_edi_payment_policy()
        for move in self.filtered(lambda item: item.move_type == 'out_invoice'):
            if move.is_advance_invoice:
                move.l10n_mx_edi_payment_policy = 'PUE'
                continue
            if move.sudo().pos_order_ids:
                continue
            orders = move.sudo().invoice_line_ids.sale_line_ids.order_id
            credit_order = 'PPD' in orders.mapped('l10n_mx_edi_payment_policy')
            if credit_order:
                move.l10n_mx_edi_payment_policy = 'PPD'

    def _is_sale_credit_invoice(self):
        self.ensure_one()
        if self.move_type != 'out_invoice' or self.is_advance_invoice or self.sudo().pos_order_ids:
            return False
        orders = self.sudo().invoice_line_ids.sale_line_ids.order_id
        return bool(orders and (self.l10n_mx_edi_payment_policy == 'PPD'
                               or 'PPD' in orders.mapped('l10n_mx_edi_payment_policy')))

    def _set_sale_invoice_dates(self):
        # The native CFDI policy uses the invoice date to infer PPD from payment terms.
        for move in self.filtered(lambda item: item.state == 'draft' and item.move_type == 'out_invoice'
                                  and not item.invoice_date and item.l10n_mx_edi_is_cfdi_needed):
            if not move.sudo().pos_order_ids and move.sudo().invoice_line_ids.sale_line_ids:
                move.check_access('write')
                move.invoice_date = fields.Date.context_today(move)

    def _sale_credit_data(self):
        self.ensure_one()
        if self.l10n_mx_edi_payment_policy != 'PPD':
            raise UserError(_('La orden está marcada a crédito. La factura debe usar PPD.'))
        partner = self.commercial_partner_id.sudo().with_company(self.company_id).with_context(
            allowed_company_ids=self.company_id.ids)
        if not partner.is_credit:
            raise UserError(_('El cliente debe tener habilitada la condición Crédito.'))
        # Force a retry if a competing credit posting used the same exposure snapshot.
        self.env.cr.execute('UPDATE res_partner SET write_date = write_date WHERE id = %s', [partner.id])
        partner.invalidate_recordset()
        date = fields.Date.context_today(self)
        amount = self.currency_id.round(max(self.amount_total, 0))
        projected = partner._pos_credit_exposure(exclude_invoice=self) + self.currency_id._convert(
            amount, partner.risk_currency_id, self.company_id, date)
        scope = json.dumps([
            partner.id, self.company_id.id, self.currency_id.id, partner.risk_currency_id.id,
            partner.credit_limit, amount, self.invoice_payment_term_id.id,
            sorted(self.sudo().invoice_line_ids.sale_line_ids.order_id.ids),
            sorted((line.advance_source_id.id, line.price_total) for line in self.invoice_line_ids if line.advance_source_id),
        ])
        requires = bool(amount and (partner.credit_limit <= 0 or partner.risk_exception
            or partner.risk_currency_id.compare_amounts(projected, partner.credit_limit) > 0))
        return partner, amount, projected, scope, requires

    def _latest_sale_credit_request(self):
        self.ensure_one()
        request = self.env['approval.request'].sudo().search([('sale_credit_move_id', '=', self.id)], order='id desc', limit=1)
        request._lock_sale_credit_requests()
        return request

    def _sale_credit_approval_covers(self, request, partner, projected, scope):
        return bool(request and request.request_status == 'approved' and not request.sale_credit_consumed
                    and request.sale_credit_scope == scope
                    and partner.risk_currency_id.compare_amounts(projected, request.sale_credit_projected) <= 0)


    def _sale_credit_category(self):
        self.ensure_one()
        Category = self.env['approval.category'].sudo()
        company = self.company_id
        domain = [('sale_credit_category', '=', True), ('company_id', '=', company.id)]
        category = Category.search(domain, limit=1)
        if category:
            return category
        self.env.cr.execute('UPDATE res_company SET write_date = write_date WHERE id = %s', [company.id])
        return Category.create({
            'name': _('Crédito excedido PPD · Ventas'), 'company_id': company.id,
            'sale_credit_category': True, 'approval_minimum': 1,
            'has_partner': 'required', 'has_amount': 'required', 'has_reference': 'required',
        })


    def _request_sale_credit(self, data, request, retry=False):
        partner, amount, projected, scope, _requires = data
        if request and not request.sale_credit_consumed:
            if request.request_status in ('refused', 'cancel') and not retry:
                return request
            if request.request_status == 'pending' and request.sale_credit_scope == scope and projected <= request.sale_credit_projected:
                return request
        users = self.env['pos.credit.approval']._eligible_users(self.company_id).filtered(
            lambda user: user.has_group('approvals.group_approval_user'))
        if not users:
            raise UserError(_('Configura un responsable de Administración, Créditos o Tesorería con acceso a Aprobaciones en esta sucursal.'))
        values = {
            'partner_id': partner.id, 'amount': amount, 'sale_credit_scope': scope,
            'sale_credit_projected': projected,
            'approver_ids': [Command.clear()] + [Command.create({'user_id': user.id, 'required': False}) for user in users],
        }
        if request and not request.sale_credit_consumed and request.request_status in ('new', 'pending', 'approved'):
            previous_amount = request.amount
            request._cancel_activities()
            request.write(values)
            request.message_post(body=_(
                'Se requiere una nueva decisión. Importe anterior %(previous)s %(currency)s. Importe actualizado %(current)s %(currency)s.',
                previous=previous_amount, current=amount, currency=self.currency_id.name,
            ))
        else:
            values['approver_ids'] = values['approver_ids'][1:]
            request = self.env['approval.request'].sudo().create(dict(
                values, name=self.env.ref('entintados_pdv.seq_pos_credit_approval').sudo().next_by_id(),
                category_id=self._sale_credit_category().id, sale_credit_move_id=self.id,
                request_owner_id=self.env.uid, reference=self.invoice_origin or self.display_name,
            ))
        request.action_confirm()
        self.message_post(body=_('Autorización de crédito %s solicitada para esta factura.', request.name))
        return request

    def _sale_credit_notification(self, message):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Crédito de Ventas'), 'message': message, 'type': 'warning', 'sticky': True}}


    def action_request_sale_credit(self):
        self.ensure_one()
        self.check_access('write')
        if self.state != 'draft' or not self._is_sale_credit_invoice():
            raise UserError(_('Selecciona una factura PPD de Ventas en borrador.'))
        data = self._sale_credit_data()
        if not data[-1]:
            return self._sale_credit_notification(_('La factura está dentro del crédito disponible. Puedes contabilizarla.'))
        request = self._latest_sale_credit_request()
        if self._sale_credit_approval_covers(request, data[0], data[2], data[3]):
            return self._sale_credit_notification(_('La autorización %s está aprobada.', request.name))
        request = self._request_sale_credit(data, request, retry=True)
        return self._sale_credit_notification(_('Solicitud %s pendiente de aprobación.', request.name))


    def action_post(self):
        self._set_sale_invoice_dates()
        for move in self.sorted(lambda record: (record.commercial_partner_id.id, record.id)):
            if move.state != 'draft' or not move._is_sale_credit_invoice():
                continue
            move.check_access('write')
            data = move._sale_credit_data()
            if data[-1]:
                request = move._latest_sale_credit_request()
                if not move._sale_credit_approval_covers(request, data[0], data[2], data[3]):
                    request = move._request_sale_credit(data, request)
                    message = (_('La solicitud %s no fue autorizada. Usa Solicitar autorización de crédito para un nuevo intento.', request.name)
                               if request.request_status in ('refused', 'cancel')
                               else _(
                                   'El cliente %(customer)s excede el límite de crédito permitido. '
                                   'El departamento correspondiente debe autorizar la solicitud %(folio)s '
                                   'con un monto de %(amount)s.',
                                   customer=move.commercial_partner_id.display_name, folio=request.name,
                                   amount=format_amount(move.env, request.amount, move.currency_id),
                               ))
                    return move._sale_credit_notification(message)
        return super().action_post()



    def _post(self, soft=True):
        self._set_sale_invoice_dates()
        consumed = []
        obsolete = []
        draft_invoices = self.filtered(lambda move: move.state == 'draft' and move.move_type == 'out_invoice')
        credit_invoices = draft_invoices.filtered(lambda move: move._is_sale_credit_invoice())
        if credit_invoices and len(draft_invoices) > 1:
            by_customer = draft_invoices.grouped(lambda move: (move.company_id, move.commercial_partner_id))
            if any(len(by_customer[move.company_id, move.commercial_partner_id]) > 1 for move in credit_invoices):
                raise UserError(_('Contabiliza por separado las facturas del mismo cliente cuando alguna sea PPD de Ventas.'))
        for move in credit_invoices.sorted(lambda record: (record.commercial_partner_id.id, record.id)):
            partner, _amount, projected, scope, requires = move._sale_credit_data()
            request = move._latest_sale_credit_request()
            if requires:
                if not move._sale_credit_approval_covers(request, partner, projected, scope):
                    raise UserError(_('La factura PPD requiere una autorización vigente. Contabilízala desde su formulario para solicitarla.'))
                consumed.append((move, request))
            elif request.request_status in ('new', 'pending', 'approved') and not request.sale_credit_consumed:
                obsolete.append((move, request))
        posted = super()._post(soft=soft)
        for move, request in consumed:
            if move.state == 'posted':
                request.write({'sale_credit_consumed': True})
        for move, request in obsolete:
            if move.state == 'posted':
                request.action_cancel()
        return posted

    def button_cancel(self):
        result = super().button_cancel()
        requests = self.filtered(lambda move: move.state == 'cancel').sudo().sale_credit_request_ids
        requests._lock_sale_credit_requests()
        for request in requests.filtered(lambda item: not item.sale_credit_consumed
                                         and item.request_status in ('new', 'pending', 'approved')):
            request.action_cancel()
        return result
