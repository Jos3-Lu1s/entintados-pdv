from odoo import api, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        company = config.company_id
        users = {employee.id: employee.user_id for employee in records.sudo()}
        for record in read_records:
            user = users.get(record['id'])
            if user:
                rights = user._get_sale_edit_rights(company)
            else:
                # Sin usuario no hay grupos que leer: solo lo que deja abierto la compañía.
                rights = {
                    'price': bool(company.sale_price_unit_editable),
                    'discount': bool(company.sale_discount_editable),
                }
            record['_can_edit_price'] = rights['price']
            record['_can_edit_discount'] = rights['discount']
        return read_records
