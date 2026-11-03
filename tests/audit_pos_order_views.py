"""Read-only inventory of POS order views and their status/button customizations."""
import json
import os
import xml.etree.ElementTree as ET

import psycopg2


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
        if database.startswith('codex_'):
            continue
        with connect(database) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.ir_ui_view')")
            if not cursor.fetchone()[0]:
                continue
            cursor.execute("""SELECT v.id, v.name, v.active, v.inherit_id, v.arch_db->>'en_US',
                v.create_date::text, v.write_date::text, u.login,
                (SELECT string_agg(d.module || '.' || d.name, ', ') FROM ir_model_data d
                 WHERE d.model='ir.ui.view' AND d.res_id=v.id)
                FROM ir_ui_view v LEFT JOIN res_users u ON u.id=v.write_uid
                WHERE v.model='pos.order' ORDER BY v.id""")
            for view_id, name, active, parent, arch, created, written, writer, xmlid in cursor.fetchall():
                root = ET.fromstring(arch or '<data/>')
                elements = []
                for element in root.iter():
                    if element.tag in ('button', 'attribute', 'xpath') or (
                            element.tag == 'field' and any(term in element.get('name', '')
                            for term in ('state', 'status', 'invoice'))):
                        elements.append({'tag': element.tag, **element.attrib,
                                         'text': (element.text or '').strip()})
                print(json.dumps({'database': database, 'id': view_id, 'name': name,
                    'active': active, 'parent': parent, 'xmlid': xmlid, 'created': created,
                    'written': written, 'writer': writer, 'elements': elements}, ensure_ascii=False))
