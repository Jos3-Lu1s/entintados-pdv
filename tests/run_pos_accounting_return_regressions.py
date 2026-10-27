"""Validación del flujo nuevo y regresiones de los flujos contables existentes."""
import test_stock_return_credit_orm as runner
import run_pos_accounting_return_checks
import run_business_chatter_regressions


def checks(env):
    print(run_pos_accounting_return_checks.checks(env), flush=True)
    print(run_business_chatter_regressions.checks(env), flush=True)
    return 'PASS: devolución de tickets y compatibilidad con ajustes, compras, chatter y permisos existentes.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
