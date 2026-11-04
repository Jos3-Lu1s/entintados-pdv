"""Read-only audit of legacy tint snapshots across local Odoo databases."""
import json
import os

import psycopg2
from psycopg2 import sql


def connect(database):
    connection = psycopg2.connect(host=os.environ['HOST'], user=os.environ['USER'],
                                 password=os.environ['PASSWORD'], dbname=database)
    connection.set_session(readonly=True, autocommit=True)
    return connection


if __name__ == '__main__':
    with connect('postgres') as connection, connection.cursor() as cursor:
        cursor.execute('SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname')
        databases = [row[0] for row in cursor.fetchall()]
    for database in databases:
        with connect(database) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.product_product'), to_regclass('public.sale_order_line')")
            if not all(cursor.fetchone()):
                continue
            cursor.execute("SELECT count(*) FROM product_product WHERE default_code = %s", ['211241'])
            matching_base = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='sale_order_line' AND column_name='tint_snapshot'")
            if not cursor.fetchone()[0]:
                print(json.dumps({'database': database, 'matching_base': matching_base, 'snapshots': 'field absent'}))
                continue
            cursor.execute("""SELECT o.state, count(*),
                count(*) FILTER (WHERE l.tint_snapshot ? 'base_only'),
                count(*) FILTER (WHERE NOT l.tint_snapshot ? 'color_name'),
                count(*) FILTER (WHERE NOT l.tint_snapshot ? 'color_code')
                FROM sale_order_line l JOIN sale_order o ON o.id=l.order_id
                WHERE l.tint_snapshot IS NOT NULL AND l.tint_snapshot != 'false'::jsonb
                GROUP BY o.state ORDER BY o.state""")
            stats = cursor.fetchall()
            cursor.execute("""SELECT l.id, l.tint_snapshot->>'color_code', l.tint_snapshot->>'color_name',
                l.tint_snapshot->>'name', l.tint_snapshot->>'base_id',
                left(l.name, 140) FROM sale_order_line l
                WHERE l.tint_snapshot IS NOT NULL AND l.tint_snapshot != 'false'::jsonb ORDER BY l.id DESC LIMIT 3""")
            print(json.dumps({'database': database, 'matching_base': matching_base, 'states': stats,
                              'samples': cursor.fetchall()}, ensure_ascii=False))
