# -*- coding: utf-8 -*-


def count_usage(env, records, references):
    """ Cuenta, por registro, cuántos registros de cada referencia lo usan.

    `references` es una lista de (modelo, campo, singular, plural). Cuenta con
    sudo() y archivados: con ondelete='restrict' o al reactivarlos, también
    importan los que el usuario no ve.
    Devuelve {id: [(conteo, singular, plural), ...]} solo con conteos > 0.
    """
    usage = {record_id: [] for record_id in records.ids}
    for model, field, singular, plural in references:
        groups = env[model].sudo().with_context(active_test=False)._read_group(
            [(field, 'in', records.ids)], [field], ['__count'],
        )
        for target, count in groups:
            usage[target.id].append((count, singular, plural))
    return {record_id: counts for record_id, counts in usage.items() if counts}


def format_usage(env, counts):
    parts = [f"{count} {singular if count == 1 else plural}" for count, singular, plural in counts]
    if len(parts) == 1:
        return parts[0]
    return env._("%(items)s y %(last)s", items=", ".join(parts[:-1]), last=parts[-1])
