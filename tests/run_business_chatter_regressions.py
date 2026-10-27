"""Seguimiento ampliado y compatibilidad con los flujos existentes; rollback final."""
import logging
import test_stock_return_credit_orm as runner
import run_business_chatter_checks
import run_customer_adjustment_checks
import run_purchase_audit_checks
import run_demo_flow_access_checks


def checks(env):
    logging.getLogger('odoo.tools.translate').setLevel(logging.ERROR)
    for module in (run_business_chatter_checks, run_customer_adjustment_checks,
                   run_purchase_audit_checks, run_demo_flow_access_checks):
        print(module.checks(env), flush=True)
    return 'PASS: chatter ampliado y regresiones de ajustes contables, compras/productos y permisos del flujo CRM.'


if __name__ == '__main__':
    runner.run_return_credit_checks = checks
    runner.run()
