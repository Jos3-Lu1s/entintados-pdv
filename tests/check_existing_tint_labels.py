"""Validate current tint data and upgrade behavior, then roll back every write."""
import json
import os
import sys

from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry
from odoo.tools import config


def protected_values(line):
    return (line.product_id.id, line.product_uom_qty, line.price_unit, line.discount,
            json.dumps(line.tint_snapshot, sort_keys=True), tuple(line.move_ids.ids), tuple(line.invoice_lines.ids))


if __name__ == '__main__':
    database = sys.argv[1]
    config.parse_config(['-c', '/etc/odoo/odoo.conf', '--db_host=' + os.environ['HOST'],
        '--db_user=' + os.environ['USER'], '--db_password=' + os.environ['PASSWORD'],
        '-d', database, '--no-http', '--max-cron-threads=0'])
    with Registry(database).cursor() as cr:
        try:
            env = api.Environment(cr, SUPERUSER_ID, {})
            lines = env['sale.order.line'].search([('tint_snapshot', '!=', False), ('product_id', '!=', False)])
            before = {line.id: protected_values(line) for line in lines}
            historical = {line.id: line.name for line in lines if line.state not in ('draft', 'sent')}
            env['sale.order']._setup_sale_tint_layout()
            for line in lines:
                assert protected_values(line) == before[line.id], 'Financial/stock/recipe data changed'
                assert line.tint_display_name == line.product_id.display_name
                expected = line.order_id._tint_color_description(line.tint_snapshot)
                assert line.tint_color_description == '+ ' + expected
                if line.id in historical:
                    assert line.name == historical[line.id], 'Historical description changed'
                elif not line.move_ids:
                    assert line.name == line.product_id.display_name + '\n+ ' + expected
            names = {line.id: line.name for line in lines}
            env['sale.order']._setup_sale_tint_layout()
            assert names == {line.id: line.name for line in lines}, 'Upgrade is not idempotent'
            delivery_checks = []
            point = env.ref('entintados_pdv.uom_tint_point')
            for line in lines:
                for move in line.move_ids.filtered(lambda item: item.state == 'done' and item.location_dest_id.usage == 'customer'):
                    containers = move.product_uom._compute_quantity(move.quantity, line.product_uom_id, round=False)
                    consumption = env['stock.move'].search([('tint_base_move_id', '=', move.id), ('state', '=', 'done')])
                    expected = sum(dose['points'] * containers for dose in line.tint_snapshot.get('doses', []))
                    actual = sum(item.product_uom._compute_quantity(item.quantity, point, round=False) for item in consumption)
                    delivery_checks.append({'move': move.id, 'expected_points': expected, 'consumed_points': actual,
                                            'matches': abs(expected - actual) < 0.00001})
            print(json.dumps({'database': database, 'checked': len(lines), 'historical_preserved': len(historical),
                'invoice_lines': len(lines.invoice_lines), 'completed_deliveries': delivery_checks,
                'previews': [{'base': line.tint_display_name, 'description': line.tint_color_description} for line in lines],
                'result': 'OK; all writes rolled back'}, ensure_ascii=False))
        finally:
            cr.rollback()
