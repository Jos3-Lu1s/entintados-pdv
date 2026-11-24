from odoo import api, models, fields

class ResUsers(models.Model):
    _inherit = 'res.users'

    digital_signature = fields.Binary(
        string="Firma Digital para Aprobaciones",
        copy=False,
        attachment=True,
        help="Firma digital del gerente/usuario utilizada para la autorización automática de documentos y salidas de almacén.",
    )

    def _get_sale_edit_rights(self, company):
        """Permiso para teclear precio unitario y descuento en una venta de ``company``: la
        compañía lo deja abierto o el usuario pertenece al grupo exento."""
        self.ensure_one()
        # En sudo: el cajero que carga la caja no siempre puede leer los grupos de otro usuario.
        user = self.sudo()
        return {
            'price': bool(company.sale_price_unit_editable
                          or user._has_group('entintados_pdv.group_sale_edit_price_unit')),
            'discount': bool(company.sale_discount_editable
                             or user._has_group('entintados_pdv.group_sale_edit_discount')),
        }

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        for record in read_records:
            rights = self.browse(record['id'])._get_sale_edit_rights(config.company_id)
            record['_can_edit_price'] = rights['price']
            record['_can_edit_discount'] = rights['discount']
        return read_records
