from collections import defaultdict

from markupsafe import Markup

from odoo import models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = 'base.partner.merge.automatic.wizard'

    def _check_merge_agreements_access(self, src_partners):
        if self.env['res.partner']._can_edit_commercial_agreements():
            return
        if src_partners.discount_rule_ids or any(
            not float_is_zero(partner.discount, precision_digits=2) for partner in src_partners
        ):
            raise AccessError(self.env._(
                'Solo los usuarios del grupo «Gestionar acuerdos comerciales» pueden fusionar '
                'contactos que tienen acuerdos comerciales o descuento.'
            ))

    def _check_source_rule_conflicts(self, src_rules):
        rules_by_target = defaultdict(lambda: self.env['res.partner.discount.rule'])
        for rule in src_rules:
            rules_by_target[rule._get_target()] |= rule
        conflicts = [(target, rules) for target, rules in rules_by_target.items() if len(rules) > 1]
        if conflicts:
            raise UserError(self.env._(
                'Varios contactos a fusionar tienen un acuerdo comercial sobre el mismo objetivo '
                'y el destino no tiene ninguno. Resuélvelo antes de fusionar:\n%(conflicts)s',
                conflicts='\n'.join(
                    '- %s: %s' % (
                        target.display_name,
                        ', '.join(rules.partner_id.mapped('display_name')),
                    )
                    for target, rules in conflicts
                ),
            ))

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        partners = self.env['res.partner'].browse(partner_ids).exists()
        if len(partners) < 2:
            return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)

        # El mismo destino que elegiría el core, para resolver los conflictos contra él.
        if not dst_partner or dst_partner not in partners:
            dst_partner = self._get_ordered_partner(partners.ids)[-1]
        src_partners = partners - dst_partner
        self._check_merge_agreements_access(src_partners)

        # El core reasigna las reglas por SQL sin pasar por la unicidad de Python:
        # en cada objetivo repetido se descarta antes la regla del origen.
        dst_targets = {rule._get_target() for rule in dst_partner.discount_rule_ids}
        src_rules = src_partners.discount_rule_ids
        discarded_rules = src_rules.filtered(lambda rule: rule._get_target() in dst_targets)
        self._check_source_rule_conflicts(src_rules - discarded_rules)
        discarded = [(rule.name, rule.partner_id.display_name) for rule in discarded_rules]
        discarded_rules.unlink()

        result = super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        if discarded:
            self._log_discarded_rules(dst_partner, discarded)
        return result

    def _log_discarded_rules(self, dst_partner, discarded):
        items = Markup().join(
            Markup('<li>%s</li>') % self.env._('%(rule)s — de %(partner)s', rule=rule_name, partner=partner_name)
            for rule_name, partner_name in discarded
        )
        dst_partner.message_post(body=Markup('<p>%s</p><ul>%s</ul>') % (
            self.env._('Acuerdos comerciales descartados en la fusión (el destino ya tenía uno sobre el mismo objetivo):'),
            items,
        ))
