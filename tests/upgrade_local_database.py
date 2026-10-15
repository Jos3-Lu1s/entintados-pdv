"""Back up and upgrade the local database affected by the schema mismatch."""
import datetime
import os
from pathlib import Path
import subprocess


if __name__ == '__main__':
    database = 'Osel'
    backup = Path('/var/lib/odoo') / ('Osel-before-advance-upgrade-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.dump')
    environment = dict(os.environ, PGPASSWORD=os.environ['PASSWORD'])
    subprocess.run([
        'pg_dump', '-h', os.environ['HOST'], '-U', os.environ['USER'],
        '-Fc', '-f', str(backup), database,
    ], env=environment, check=True)
    if not backup.is_file() or not backup.stat().st_size:
        raise SystemExit('Backup missing; upgrade aborted')
    print('Backup:', backup, 'bytes:', backup.stat().st_size, flush=True)
    result = subprocess.call([
        'odoo', '--db_host=' + os.environ['HOST'], '--db_user=' + os.environ['USER'],
        '--db_password=' + os.environ['PASSWORD'], '-d', database,
        '-u', 'entintados_pdv', '--stop-after-init', '--no-http', '--max-cron-threads=0',
        '--log-handler=odoo.modules.loading:WARNING',
    ])
    raise SystemExit(result)
