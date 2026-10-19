"""Install only the new XML data, preserving existing cron preferences."""
from odoo import api, SUPERUSER_ID
from odoo.tools import config, convert
from odoo.modules.registry import Registry

config.parse_config(['-c', '/etc/odoo/odoo.conf', '-d', 'Osel_Test', '--no-http'])
registry = Registry('Osel_Test')
with registry.cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref('entintados_pdv.ir_cron_update_payment_cfdi', raise_if_not_found=False)
    if not cron:
        convert.convert_file(env, 'entintados_pdv', 'data/account_payment_cfdi_cron.xml',
                             {}, mode='init', noupdate=True, kind='data')
        cron = env.ref('entintados_pdv.ir_cron_update_payment_cfdi')
        cr.commit()
    print('Cron registered:', cron.id, cron.name, 'active=', cron.active,
          'interval=', cron.interval_number, cron.interval_type, 'nextcall UTC=', cron.nextcall)
