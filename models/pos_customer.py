from odoo import _, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _check_pos_customer(self):
        if any(not order.partner_id for order in self):
            raise UserError(_('Selecciona primero un cliente para iniciar la compra.'))

    def _process_saved_order(self, draft):
        if self.lines:
            self._check_pos_customer()
        return super()._process_saved_order(draft)

    def action_pos_order_paid(self):
        self._check_pos_customer()
        return super().action_pos_order_paid()
