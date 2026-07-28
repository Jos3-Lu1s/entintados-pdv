# -*- coding: utf-8 -*-

from odoo import models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    def _prepare_customer_values(self, partner_name, is_company=False, parent_id=False):
        res = super()._prepare_customer_values(partner_name, is_company=is_company, parent_id=parent_id)
        res['is_customer'] = True
        return res
