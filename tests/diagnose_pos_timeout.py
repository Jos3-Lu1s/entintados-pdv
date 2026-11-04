"""Read-only PostgreSQL snapshot for POS timeout investigations."""
import re
import psycopg2
from odoo.tools import config

config.parse_config(['-c', '/etc/odoo/odoo.conf'])
args = dict(user=config['db_user'], password=config['db_password'], host=config['db_host'], port=config['db_port'])
with psycopg2.connect(dbname='postgres', **args) as connection:
    with connection.cursor() as cr:
        cr.execute("""SELECT datname, pid, state, wait_event_type, wait_event,
            EXTRACT(EPOCH FROM (now()-query_start))::int, pg_blocking_pids(pid), left(query, 1200)
            FROM pg_stat_activity WHERE datname IS NOT NULL AND pid != pg_backend_pid()
            AND state != 'idle' ORDER BY query_start""")
        for row in cr.fetchall():
            print('ACTIVITY', row[:-1], re.sub(r"'[^']*'", "'?'", row[-1]))
        cr.execute("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname != 'postgres'")
        names = [row[0] for row in cr.fetchall()]
for name in names:
    with psycopg2.connect(dbname=name, **args) as connection:
        with connection.cursor() as cr:
            cr.execute("SELECT to_regclass('ir_module_module')")
            if not cr.fetchone()[0]: continue
            cr.execute("SELECT state FROM ir_module_module WHERE name='entintados_pdv'")
            print('MODULE', name, cr.fetchall())
            cr.execute("SELECT to_regclass('pos_order')")
            if not cr.fetchone()[0]: continue
            cr.execute("SELECT id, name, state, to_invoice, account_move, date_order, write_date FROM pos_order ORDER BY id DESC LIMIT 8")
            print('ORDERS', name, cr.fetchall())
            cr.execute("SELECT key, value FROM ir_config_parameter WHERE key IN ('web.base.url','report.url','web.base.url.freeze')")
            print('REPORT_URLS', name, cr.fetchall())
            cr.execute("SELECT id, name, move_type, state, create_date FROM account_move ORDER BY id DESC LIMIT 5")
            print('MOVES', name, cr.fetchall())
