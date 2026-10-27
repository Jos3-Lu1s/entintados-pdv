from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ResPartnerSaleOwner(models.Model):
    _inherit = 'res.partner'

    # Odoo's editable compute inherits the parent salesperson. Once ownership
    # is fixed, reparenting a contact must not silently replace that owner.
    user_id = fields.Many2one(readonly=True, compute=False, precompute=False)
    sale_owner_name = fields.Char(compute='_compute_sale_owner_name')

    def _contact_sale_owner(self):
        self.ensure_one()
        return self.user_id or self.commercial_partner_id.user_id

    @api.depends('user_id.name', 'commercial_partner_id.user_id.name')
    def _compute_sale_owner_name(self):
        for partner in self:
            partner.sale_owner_name = partner._contact_sale_owner().name or ''

    def write(self, vals):
        if 'user_id' in vals and any(partner.user_id.id != (vals['user_id'] or False) for partner in self):
            raise UserError(_('El vendedor está vinculado al contacto y no puede modificarse.'))
        return super().write(vals)

    @api.model
    def _load_pos_data_fields(self, config):
        return list(dict.fromkeys(super()._load_pos_data_fields(config) + ['sale_owner_name']))


class SaleOrderContactOwner(models.Model):
    _inherit = 'sale.order'

    user_id = fields.Many2one(readonly=True)

    @api.depends('partner_id', 'partner_id.user_id', 'partner_id.commercial_partner_id.user_id')
    def _compute_user_id(self):
        for order in self:
            order.user_id = order.partner_id._contact_sale_owner() if order.partner_id else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            partner_id = vals.get('partner_id') or self.env.context.get('default_partner_id')
            if partner_id:
                vals['user_id'] = self.env['res.partner'].browse(partner_id)._contact_sale_owner().id
        return super().create(vals_list)

    def write(self, vals):
        if 'partner_id' in vals:
            vals = dict(vals, user_id=self.env['res.partner'].browse(vals['partner_id'])._contact_sale_owner().id
                        if vals['partner_id'] else False)
        elif 'user_id' in vals and any(order.partner_id and vals['user_id'] != order.partner_id._contact_sale_owner().id for order in self):
            raise UserError(_('El vendedor de la venta debe ser el asignado al contacto.'))
        return super().write(vals)


class CrmLeadContactOwner(models.Model):
    _inherit = 'crm.lead'

    @api.onchange('partner_id')
    def _onchange_contact_sale_owner(self):
        for lead in self.filtered('partner_id'):
            lead.user_id = lead.partner_id._contact_sale_owner()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            partner_id = vals.get('partner_id') or self.env.context.get('default_partner_id')
            if partner_id:
                vals['user_id'] = self.env['res.partner'].browse(partner_id)._contact_sale_owner().id
        return super().create(vals_list)

    def write(self, vals):
        if 'partner_id' in vals and vals['partner_id']:
            vals = dict(vals, user_id=self.env['res.partner'].browse(vals['partner_id'])._contact_sale_owner().id)
        elif 'user_id' in vals and any(lead.partner_id and vals['user_id'] != lead.partner_id._contact_sale_owner().id for lead in self):
            raise UserError(_('El vendedor de la oportunidad debe ser el asignado al contacto.'))
        return super().write(vals)


class PosOrderContactOwner(models.Model):
    _inherit = 'pos.order'

    user_id = fields.Many2one(readonly=True)
    contact_salesperson_id = fields.Many2one('res.users', compute='_compute_contact_salesperson',
                                            store=True, readonly=True, string='Vendedor del contacto')

    @api.depends('partner_id.user_id', 'partner_id.commercial_partner_id.user_id')
    def _compute_contact_salesperson(self):
        for order in self:
            order.contact_salesperson_id = order.partner_id._contact_sale_owner() if order.partner_id else False

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        vals['invoice_user_id'] = self.contact_salesperson_id.id
        return vals

    def _create_invoice(self, move_vals):
        invoice = super()._create_invoice(move_vals)
        # Split invoices use a billing address and only the first has the
        # native POS inverse. Keep the seller of the actual selling contact.
        (invoice | self.split_invoice_ids).write({'invoice_user_id': self.contact_salesperson_id.id})
        return invoice

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.pop('contact_salesperson_id', None)
        return super().create(vals_list)

    def write(self, vals):
        if 'contact_salesperson_id' in vals:
            if any(vals['contact_salesperson_id'] != (
                    self.env['res.partner'].browse(vals['partner_id'])._contact_sale_owner().id
                    if vals.get('partner_id') else order.contact_salesperson_id.id) for order in self):
                raise UserError(_('El vendedor del punto de venta está vinculado al contacto.'))
            vals = {key: value for key, value in vals.items() if key != 'contact_salesperson_id'}
        return super().write(vals)


class AccountMoveContactOwner(models.Model):
    _inherit = 'account.move'

    invoice_user_id = fields.Many2one(readonly=True)

    def _sale_contact_owner(self):
        self.ensure_one()
        if self.move_type == 'out_refund' and self.reversed_entry_id.move_type == 'out_invoice':
            return self.reversed_entry_id.invoice_user_id
        orders = self.invoice_line_ids.sale_line_ids.order_id
        owners = orders.user_id | self.pos_order_ids.contact_salesperson_id
        if not owners and self.id:
            split_orders = self.env['pos.order'].sudo().search([
                ('company_id', '=', self.company_id.id), ('split_invoice_ids', 'in', self.ids)])
            owners = split_orders.contact_salesperson_id
        if len(owners) == 1:
            return owners
        return self.partner_id._contact_sale_owner() if self.partner_id else self.env['res.users']

    @api.onchange('partner_id')
    def _onchange_contact_sale_owner(self):
        for move in self.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund')):
            move.invoice_user_id = move._sale_contact_owner()

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund') and m.partner_id):
            owner = move._sale_contact_owner()
            if move.invoice_user_id != owner:
                super(AccountMoveContactOwner, move).write({'invoice_user_id': owner.id})
        return moves

    def write(self, vals):
        if 'invoice_user_id' in vals and 'partner_id' not in vals and any(
                move.move_type in ('out_invoice', 'out_refund') and move.partner_id
                and vals['invoice_user_id'] != move._sale_contact_owner().id for move in self):
            raise UserError(_('El vendedor de la factura debe corresponder al contacto de la venta.'))
        result = super().write(vals)
        if 'partner_id' in vals:
            for move in self.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund')):
                super(AccountMoveContactOwner, move).write({'invoice_user_id': move._sale_contact_owner().id})
        return result
