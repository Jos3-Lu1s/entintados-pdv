import math

from odoo import api, Command, fields, models, _
from odoo.exceptions import UserError, ValidationError


class VendorCreditGroupMaterial(models.Model):
    _inherit = 'vendor.credit.group'

    operation_type = fields.Selection(related='reason_id.operation_type')
    has_material_returns = fields.Boolean(compute='_compute_has_material_returns')

    @api.depends('line_ids.selected', 'line_ids.effective_reason_id', 'line_ids.operation_type',
                 'line_ids.selected_reason_id', 'reason_id.operation_type',
                 'line_ids.selected_reason_id.operation_type')
    def _compute_has_material_returns(self):
        for group in self:
            group.has_material_returns = any(line.operation_type == 'return'
                                             for line in group.line_ids.filtered('selected'))
    material_line_ids = fields.One2many('vendor.credit.material', 'group_id', string='Material a devolver', copy=False)
    return_picking_ids = fields.Many2many('stock.picking', string='Devoluciones a proveedor', readonly=True,
                                        copy=False, check_company=True)

    @api.model_create_multi
    def create(self, vals_list):
        if any(values.get('return_picking_ids') for values in vals_list):
            raise UserError(_('Las devoluciones se asignan al generar las notas.'))
        return super().create(vals_list)

    def write(self, values):
        if 'return_picking_ids' in values:
            raise UserError(_('Las devoluciones se asignan al generar las notas.'))
        if 'material_line_ids' in values and any(group.state != 'draft' or group.credit_ids for group in self):
            raise UserError(_('El material solo se modifica en borrador.'))
        return super().write(values)

    def action_view_material_returns(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Devoluciones a proveedor'),
                'res_model': 'stock.picking', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.return_picking_ids.ids)]}

    def _validate_material_selection(self):
        self.ensure_one()
        selected = self.line_ids.filtered(lambda line: line.selected and line.operation_type == 'return')
        if not selected:
            if self.material_line_ids:
                raise UserError(_('El motivo monetario no admite material. Elimina las líneas de devolución o cambia el motivo.'))
            return
        if self.material_line_ids.allocation_id != selected:
            # Recordsets may have different order; only membership matters.
            if set(self.material_line_ids.allocation_id.ids) != set(selected.ids):
                raise UserError(_('Captura material para cada factura seleccionada y elimina el de las facturas excluidas.'))
        self.material_line_ids._check_material()
        self.material_line_ids._return_quantities()

    def _generate_material_returns(self):
        if not self.has_material_returns:
            super(VendorCreditGroupMaterial, self).write({'return_picking_ids': [Command.clear()]})
            return
        quantities = self.material_line_ids._return_quantities()
        pickings = self.env['stock.picking']
        for picking in self.material_line_ids.receipt_move_id.picking_id:
            wizard = self.env['stock.return.picking'].create({'picking_id': picking.id})
            for line in wizard.product_return_moves:
                line.quantity = quantities.get(line.move_id.id, 0)
            returned = wizard._create_return()
            returned.origin = '%s — %s' % (self.name, picking.name)
            returned.message_post(body=_('Devolución generada por la nota distribuida %s. Valida esta transferencia para registrar la salida física.') % self.name)
            pickings |= returned
        super(VendorCreditGroupMaterial, self).write({'return_picking_ids': [Command.set(pickings.ids)]})

    def action_cancel(self):
        with self.env.cr.savepoint():
            self.env['res.company']._with_locked_records(self)
            returns = self.return_picking_ids
            self.env['res.company']._with_locked_records(returns)
            returns.invalidate_recordset()
            if any(picking.state == 'done' for picking in returns):
                raise UserError(_('La agrupación tiene devoluciones realizadas. Utiliza un ajuste de importe o el proceso de reversión contable y de inventario; no se puede cancelar su efecto físico desde esta agrupación.'))
            returns.filtered(lambda picking: picking.state != 'cancel').action_cancel()
            return super().action_cancel()


class VendorCreditMaterial(models.Model):
    _name = 'vendor.credit.material'
    _description = 'Material de nota distribuida de proveedor'
    _check_company_auto = True

    group_id = fields.Many2one('vendor.credit.group', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='group_id.company_id', store=True)
    allocation_id = fields.Many2one('vendor.credit.group.line', string='Factura seleccionada', required=True, ondelete='cascade')
    invoice_id = fields.Many2one(related='allocation_id.invoice_id', string='Factura')
    invoice_line_id = fields.Many2one('account.move.line', string='Producto facturado', required=True,
                                      ondelete='restrict', check_company=True)
    product_id = fields.Many2one(related='invoice_line_id.product_id')
    product_uom_id = fields.Many2one(related='invoice_line_id.product_uom_id', string='Unidad')
    quantity = fields.Float(string='Cantidad a devolver', digits='Product Unit', required=True, default=1)
    receipt_move_id = fields.Many2one('stock.move', string='Recepción de origen', required=True,
                                      ondelete='restrict', check_company=True)

    @api.constrains('group_id', 'allocation_id', 'invoice_line_id', 'receipt_move_id', 'quantity')
    def _check_material(self):
        for item in self:
            line, move = item.invoice_line_id, item.receipt_move_id
            if (item.allocation_id.group_id != item.group_id or line.move_id != item.invoice_id
                    or not line.product_id or line.display_type != 'product' or line.quantity <= 0
                    or not math.isfinite(item.quantity) or item.quantity <= 0
                    or line.product_uom_id.compare(item.quantity, line.quantity) > 0):
                raise ValidationError(_('Selecciona un producto de la factura y una cantidad positiva que no supere la facturada.'))
            if (move.state != 'done' or not move.picking_id or move.company_id != item.company_id
                    or move.product_id != item.product_id or move.location_id.usage != 'supplier'
                    or move.location_dest_id.usage != 'internal'
                    or move.picking_id.partner_id.commercial_partner_id != item.group_id.partner_id.commercial_partner_id
                    or (line.purchase_line_id and move.purchase_line_id != line.purchase_line_id)):
                raise ValidationError(_('La recepción debe estar realizada y corresponder al proveedor, compañía y producto facturado (y su línea de compra, cuando exista).'))

    def _return_quantities(self):
        moves = self.receipt_move_id
        self.env['res.company']._with_locked_records(moves)
        moves.invalidate_recordset()
        quantities = {}
        for item in self:
            move = item.receipt_move_id
            quantity = item.product_uom_id._compute_quantity(item.quantity, move.product_uom, round=False)
            quantities[move.id] = quantities.get(move.id, 0) + quantity
        for move in moves:
            used = sum(returned.product_uom._compute_quantity(
                returned.quantity if returned.state == 'done' else returned.product_uom_qty,
                move.product_uom, round=False) for returned in move.returned_move_ids if returned.state != 'cancel')
            if move.product_uom.compare(quantities[move.id], move.quantity - used) > 0:
                raise UserError(_('La cantidad de %s supera lo recibido disponible para devolver; considera también las devoluciones pendientes.') % move.product_id.display_name)
        for line in self.invoice_line_id:
            items = self.filtered(lambda item: item.invoice_line_id == line)
            if line.product_uom_id.compare(sum(items.mapped('quantity')), line.quantity) > 0:
                raise UserError(_('La cantidad total a devolver supera la cantidad facturada de %s.') % line.product_id.display_name)
        return quantities

    def _check_draft(self):
        if any(group.state != 'draft' or group.credit_ids for group in self.group_id):
            raise UserError(_('El material solo se modifica en una agrupación en borrador.'))

    @api.model_create_multi
    def create(self, values_list):
        records = super().create(values_list)
        records._check_draft()
        return records

    def write(self, values):
        self._check_draft()
        result = super().write(values)
        self._check_draft()
        return result

    def unlink(self):
        self._check_draft()
        return super().unlink()
