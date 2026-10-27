from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class PosOrderBranchReference(models.Model):
    _inherit = 'pos.order'

    branch_order_number = fields.Integer(copy=False, readonly=True, index=True)
    previous_order_reference = fields.Char(
        string='Referencia anterior', copy=False, readonly=True)

    @api.model
    def _next_branch_order_reference(self, company):
        # Serialize sequence creation as well as numbering across all tills of a branch.
        self.env.cr.execute('SELECT id FROM res_company WHERE id = %s FOR UPDATE', [company.id])
        sequences = self.env['ir.sequence'].sudo()
        sequence = sequences.search([
            ('code', '=', 'entintados_pdv.branch_order'),
            ('company_id', '=', company.id),
        ], limit=1)
        if not sequence:
            sequence = sequences.create({
                'name': f'Órdenes PdV · {company.name}',
                'code': 'entintados_pdv.branch_order',
                'company_id': company.id,
                'implementation': 'no_gap',
                'padding': 4,
            })
        number = int(sequence.next_by_id())
        return number, f'{company.name} - PDV-{number:04d}'

    @api.model_create_multi
    def create(self, vals_list):
        values = []
        for original in vals_list:
            vals = dict(original)
            session = self.env['pos.session'].browse(
                vals.get('session_id') or self.env.context.get('default_session_id'))
            company = session.company_id or self.env.company
            number, reference = self._next_branch_order_reference(company)
            vals.update(name=reference, branch_order_number=number,
                        previous_order_reference=False)
            values.append(vals)
        return super().create(values)

    def write(self, vals):
        # Offline clients may send the old name again when synchronizing a ticket.
        if {'name', 'branch_order_number', 'previous_order_reference'} & vals.keys():
            for order in self:
                values = dict(vals)
                if order.branch_order_number:
                    values.pop('name', None)
                    values.pop('branch_order_number', None)
                    values.pop('previous_order_reference', None)
                super(PosOrderBranchReference, order).write(values)
            return True
        return super().write(vals)

    @api.model
    def _migrate_branch_order_references(self):
        if not self.env.su:
            raise AccessError(_('Solo el administrador puede migrar las referencias del PdV.'))
        count = 0
        for company in self.env['res.company'].sudo().search([], order='id'):
            orders = self.sudo().search([
                ('company_id', '=', company.id), ('branch_order_number', '=', 0),
            ], order='date_order, id')
            for order in orders:
                number, reference = self._next_branch_order_reference(company)
                super(PosOrderBranchReference, order).write({
                    'name': reference, 'branch_order_number': number,
                    'previous_order_reference': order.name,
                })
                count += 1
        return count
