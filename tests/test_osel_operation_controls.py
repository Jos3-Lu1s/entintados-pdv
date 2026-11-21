from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.addons.entintados_pdv.models.branch_purchase_request import StockPickingBranchReceipt


@tagged('post_install', '-at_install')
class TestOselOperationControls(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            'stock_control_department_id': cls.env.ref('entintados_pdv.department_stock_control').id,
            'stock_require_signature': False, 'stock_require_receipt_evidence': False,
            'branch_supply_center': True,
        })
        cls.destination_company = cls.env['res.company'].create({
            'name': 'Sucursal destino pruebas', 'parent_id': cls.company.id,
        })
        cls.source = cls.env['stock.warehouse'].create({
            'name': 'Principal', 'code': 'TSPR', 'company_id': cls.company.id,
        })
        cls.destination = cls.env['stock.warehouse'].create({
            'name': 'Sucursal prueba', 'code': 'TSSU', 'company_id': cls.destination_company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Material de prueba', 'type': 'consu', 'is_storable': True,
            'purchase_ok': True, 'standard_price': 10,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Proveedor de prueba', 'is_supplier': True, 'phone': '5551234567',
        })
        cls.worker = new_test_user(cls.env, login='osel_stock_control_test',
                                   groups='base.group_user,stock.group_stock_user,purchase.group_purchase_user')

    def _request(self, quantity=5):
        return self.env['branch.purchase.request'].create({
            'company_id': self.destination_company.id, 'source_company_id': self.company.id,
            'source_warehouse_id': self.source.id, 'warehouse_id': self.destination.id,
            'partner_id': self.partner.id,
            'line_ids': [Command.create({'product_id': self.product.id, 'quantity': quantity})],
        })

    def _purchase(self, quantity=5):
        return self.env['purchase.order'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'picking_type_id': self.source.in_type_id.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_qty': quantity, 'price_unit': 10})],
        })

    def test_department_authorization_and_edit_invalidation(self):
        request = self._request()
        request.action_send_request()
        request.action_start_transfer()
        picking = request.picking_id
        with self.assertRaises(UserError):
            picking._check_operation_authorizations()
        with self.assertRaises(UserError):
            self.company.with_user(self.worker)._check_stock_control_department()
        self.env['hr.employee'].create({
            'name': self.worker.name, 'user_id': self.worker.id, 'company_id': self.company.id,
            'department_id': self.company.stock_control_department_id.id,
        })
        self.company.with_user(self.worker)._check_stock_control_department()
        picking.action_approve_internal_transfer()
        picking._check_operation_authorizations()
        picking.move_ids.product_uom_qty = 6
        with self.assertRaises(UserError):
            picking._check_operation_authorizations()
        with self.assertRaises(UserError):
            picking.write({'transfer_approved_by': self.worker.id})

    def test_partial_cancelled_backorder_requires_explicit_closure(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 5)
        request = self._request()
        request.action_send_request()
        request.action_start_transfer()
        picking = request.picking_id
        picking.action_assign()
        picking.action_approve_internal_transfer()
        picking.move_ids.quantity = 2
        picking.move_ids.picked = True
        picking.with_context(cancel_backorder=True)._action_done()
        self.assertEqual(request.line_ids.quantity_done, 2)
        self.assertEqual(request.line_ids.quantity_pending, 3)
        self.assertEqual(request.state, 'progress')
        request.closure_reason = 'Se cierra el faltante con autorización'
        request.action_close_remainder()
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.line_ids.quantity_pending, 3)

    def test_manual_purchase_uses_only_shortage_and_preserves_origin(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 2)
        request = self._request()
        action = request.action_create_purchase()
        purchase = request.purchase_id
        self.assertEqual(action['res_id'], purchase.id)
        self.assertEqual(purchase.order_line.product_qty, 3)
        self.assertEqual(purchase.branch_request_id, request)
        self.assertEqual(purchase.picking_type_id, self.source.in_type_id)
        self.assertIn(request.name, purchase.origin)
        self.assertEqual(request.action_create_purchase()['res_id'], purchase.id)
        html, _kind = self.env['ir.actions.report']._render_qweb_html(
            'entintados_pdv.action_report_branch_requisition', request.ids)
        self.assertIn(request.requisition_ref.encode(), html)
        self.assertIn(request.name.encode(), html)

    def test_reservations_cannot_allocate_the_same_stock_twice(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 5)
        requests = self._request(4) | self._request(4)
        for request in requests:
            request.action_send_request()
            request.action_start_transfer()
            request.picking_id.action_assign()
        self.assertEqual(sum(requests.picking_id.move_ids.mapped('quantity')), 5)
        self.assertEqual(requests[0].line_ids.source_free_qty, 0)

    def test_all_together_delivery_policy_blocks_partial_validation(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 5)
        request = self._request()
        request.delivery_policy = 'one'
        request.action_send_request()
        request.action_start_transfer()
        picking = request.picking_id
        picking.action_assign()
        picking.action_approve_internal_transfer()
        picking.move_ids.quantity = 2
        picking.move_ids.picked = True
        with self.assertRaises(UserError):
            picking._action_done()

    def test_approval_requires_signature_when_configured(self):
        request = self._request()
        request.action_send_request()
        self.company.stock_require_signature = True
        with self.assertRaises(UserError):
            request.picking_id.action_approve_internal_transfer()

    def test_internal_move_line_cannot_finish_in_its_source_location(self):
        picking = self.env['stock.picking'].create({
            'company_id': self.company.id, 'picking_type_id': self.source.int_type_id.id,
            'location_id': self.source.lot_stock_id.id, 'location_dest_id': self.source.wh_input_stock_loc_id.id,
        })
        line = self.env['stock.move.line'].create({
            'picking_id': picking.id,
            'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id, 'quantity': 1,
            'location_id': self.source.lot_stock_id.id, 'location_dest_id': self.source.lot_stock_id.id,
        })
        with self.assertRaises(ValidationError):
            line._action_done()

    def test_return_copy_keeps_native_origin_without_reopening_request(self):
        request = self._request()
        request.action_send_request()
        move = request.picking_id.move_ids
        values = move.copy_data(default={'origin_returned_move_id': move.id})[0]
        self.assertFalse(values['branch_request_line_id'])
        self.assertEqual(values['origin_returned_move_id'], move.id)

    def test_physical_review_tracks_current_quantities_and_evidence(self):
        purchase = self._purchase()
        purchase.button_confirm()
        picking = purchase.picking_ids
        picking.move_ids.quantity = 5
        picking.move_ids.picked = True
        with self.assertRaises(UserError):
            picking._check_operation_authorizations()
        self.company.stock_require_receipt_evidence = True
        with self.assertRaises(UserError):
            picking.action_review_purchase_receipt()
        attachment = self.env['ir.attachment'].create({
            'name': 'Revisión física', 'datas': 'eA==', 'res_model': 'stock.picking', 'res_id': picking.id,
        })
        picking.evidence_attachment_ids = attachment
        picking.action_review_purchase_receipt()
        picking._check_operation_authorizations()
        picking.move_ids.quantity = 4
        with self.assertRaises(UserError):
            picking._check_operation_authorizations()
        picking.move_ids.quantity = 5
        picking.action_review_purchase_receipt()
        picking._action_done()
        self.assertEqual(purchase.order_line.qty_received, 5)

    def test_clarification_deadline_and_activity(self):
        self.company.stock_clarification_days = 3
        defaults = self.env['stock.picking'].default_get(['clarification_days'])
        self.assertEqual(defaults['clarification_days'], 3)
        request = self._request()
        request.action_send_request()
        picking = request.picking_id
        self.assertEqual(picking.clarification_days, 3)
        picking.write({
            'clarification_days': 3, 'clarification_notes': 'Diferencia en el material',
            'clarification_responsible_id': self.worker.id,
        })
        picking.action_open_clarification()
        self.assertEqual(picking.clarification_deadline, fields.Date.context_today(picking) + timedelta(days=3))
        self.assertTrue(picking.activity_ids.filtered(lambda item: item.user_id == self.worker))
        picking.clarification_resolution = 'Cantidad revisada y aclarada'
        picking.action_close_clarification()
        self.assertEqual(picking.clarification_state, 'closed')

    def test_purchase_catalog_and_confirmed_edit_permissions(self):
        purchase = self._purchase()
        self.product.purchase_line_product = False
        with self.assertRaises(ValidationError):
            purchase.button_confirm()
        self.product.purchase_line_product = True
        purchase.button_confirm()
        with self.assertRaises(UserError):
            purchase.order_line.with_user(self.worker).write({'price_unit': 25})

    def test_supplier_duplicate_reference_cannot_be_posted_twice(self):
        payable = self.env['account.account'].create({
            'name': 'Cuentas por pagar pruebas', 'code': 'TSCXP', 'account_type': 'liability_payable',
            'reconcile': True, 'company_ids': [Command.set(self.company.ids)],
        })
        expense = self.env['account.account'].create({
            'name': 'Gasto pruebas', 'code': 'TSGAS', 'account_type': 'expense',
            'company_ids': [Command.set(self.company.ids)],
        })
        self.partner.property_account_payable_id = payable
        journal = self.env['account.journal'].create({
            'name': 'Compras pruebas', 'code': 'TSCO', 'type': 'purchase', 'company_id': self.company.id,
        })
        values = {
            'partner_id': self.partner.id, 'move_type': 'in_invoice', 'ref': 'REF-CONTROL-001',
            'invoice_date': fields.Date.today(), 'journal_id': journal.id,
            'invoice_line_ids': [Command.create({
                'name': 'Material', 'quantity': 1, 'price_unit': 10,
                'account_id': expense.id, 'tax_ids': [Command.clear()],
            })],
        }
        first = self.env['account.move'].create(values)
        first.action_post()
        second = self.env['account.move'].create(values)
        with self.assertRaises(ValidationError):
            second.action_post()

    def test_invoice_receipt_difference_policy_is_configurable(self):
        purchase = self._purchase()
        expense = self.env['account.account'].search([
            ('account_type', '=', 'expense'), ('company_ids', 'in', self.company.ids),
        ], limit=1)
        self.product.property_account_expense_id = expense
        purchase.button_confirm()
        invoice_values = purchase._prepare_invoice()
        invoice_values.update({
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'product_id': self.product.id, 'name': 'Prueba de comparación',
                'purchase_line_id': purchase.order_line.id,
                'account_id': expense.id, 'quantity': 5, 'price_unit': 10,
                'tax_ids': [Command.clear()],
            })],
        })
        invoice = self.env['account.move'].create(invoice_values)
        self.assertTrue(invoice.purchase_receipt_has_difference)
        self.company.purchase_receipt_difference_policy = 'block'
        with self.assertRaises(ValidationError):
            invoice.action_post()
        self.company.purchase_receipt_difference_policy = 'warn'
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')

    def test_margin_uses_discounted_untaxed_revenue_and_existing_cost(self):
        seller = new_test_user(
            self.env(context={**self.env.context, 'no_reset_password': True}),
            login='osel_margin_test', groups='base.group_user,sales_team.group_sale_salesman',
        )
        customer = self.env['res.partner'].create({
            'name': 'Cliente de prueba de margen', 'is_customer': True,
            'phone': '5551234578', 'user_id': seller.id,
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id, 'warehouse_id': self.source.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'product_uom_qty': 2,
                'price_unit': 200, 'discount': 25, 'tax_ids': [Command.clear()],
            })],
        })
        line = order.order_line
        self.assertAlmostEqual(line.osel_cost_amount, 20)
        self.assertAlmostEqual(line.osel_margin_amount, 280)
        self.assertAlmostEqual(line.osel_margin_ratio, 280 / 300)
        order.with_user(seller).check_access('read')
        self.company.write({'sale_margin_control': True, 'sale_minimum_margin': 0.95})
        with self.assertRaisesRegex(UserError, 'responsable de Ventas por su margen'):
            order.with_user(seller).action_confirm()

    def _finish_outbound(self, picking, quantity):
        picking.action_assign()
        picking.action_approve_internal_transfer()
        picking.move_ids.quantity = quantity
        picking.move_ids.picked = True
        picking._action_done()

    def test_branch_roles_follow_company_instead_of_warehouse_name(self):
        self.source.name = 'Almacén genérico'
        self.destination.name = 'Principal'
        self.assertTrue(self.source.branch_supply_center)
        self.assertFalse(self.destination.branch_supply_center)
        self.assertTrue(self.destination.branch_requisition_allowed)

    def test_branch_configuration_is_editable_from_parent_and_used_by_documents(self):
        from lxml import etree
        self.company.branch_warehouse_id = self.source
        self.company.write({'child_ids': [Command.update(self.destination_company.id, {
            'branch_warehouse_id': self.destination.id, 'branch_supply_center': False,
        })]})
        request = self.env['branch.purchase.request'].new({
            'company_id': self.destination_company.id, 'source_company_id': self.company.id,
        })
        request._onchange_default_supply_center()
        self.assertEqual(request.source_warehouse_id, self.source)
        self.assertEqual(request.warehouse_id, self.destination)
        purchase_defaults = self.env['purchase.order'].default_get(['picking_type_id'])
        self.assertEqual(purchase_defaults['picking_type_id'], self.source.in_type_id.id)
        purchase = self.env['purchase.order'].with_company(self.destination_company).create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
        })
        self.assertEqual(purchase.picking_type_id, self.source.in_type_id)
        arch = etree.fromstring(self.env['res.company'].get_view(
            self.env.ref('base.view_company_form').id, 'form')['arch'])
        for field in ('branch_supply_center', 'branch_warehouse_id', 'branch_requisition_allowed'):
            self.assertTrue(arch.xpath("//field[@name='child_ids']/list/field[@name='%s']" % field))
        with self.assertRaises(ValidationError):
            self.destination_company.branch_warehouse_id = self.source

    def test_multi_company_transfer_changes_both_stocks_without_invoices(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 10)
        commercial_before = (self.env['sale.order'].search_count([]), self.env['purchase.order'].search_count([]),
                             self.env['account.move'].search_count([('move_type', 'in', ['in_invoice', 'out_invoice'])]))
        request = self._request()
        request.action_send_request()
        request.action_start_transfer()
        self._finish_outbound(request.picking_id, 5)
        receipt = request.picking_ids.filtered(lambda picking: picking.branch_direction == 'inbound')
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.company_id, self.destination_company)
        self.assertEqual(receipt.branch_source_picking_id, request.picking_id)
        self.assertEqual(request.line_ids.quantity_done, 5)
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_id._receive_interbranch_transfer(), receipt)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.source.lot_stock_id), 5)
        self.assertEqual(self.env['stock.quant'].with_company(self.destination_company)._get_available_quantity(
            self.product, self.destination.lot_stock_id), 5)
        self.assertEqual(commercial_before, (
            self.env['sale.order'].search_count([]), self.env['purchase.order'].search_count([]),
            self.env['account.move'].search_count([('move_type', 'in', ['in_invoice', 'out_invoice'])]),
        ))

    def test_multi_company_partial_transfer_keeps_backorder_and_correct_totals(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 5)
        request = self._request()
        request.action_send_request()
        request.action_start_transfer()
        self._finish_outbound(request.picking_id, 2)
        self.assertEqual(request.line_ids.quantity_done, 2)
        self.assertEqual(request.line_ids.quantity_pending, 3)
        self.assertEqual(request.state, 'progress')
        backorder = request.picking_ids.filtered(
            lambda picking: picking.branch_direction == 'outbound' and picking.state not in ('done', 'cancel'))
        self.assertEqual(len(backorder), 1)
        self._finish_outbound(backorder, 3)
        self.assertEqual(request.line_ids.quantity_done, 5)
        self.assertEqual(request.state, 'done')
        self.assertEqual(request.picking_count, 4)

    def test_destination_failure_rolls_back_source_movement(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 5)
        request = self._request()
        request.action_send_request()
        request.action_start_transfer()
        picking = request.picking_id
        picking.action_assign()
        picking.action_approve_internal_transfer()
        picking.move_ids.quantity = 5
        picking.move_ids.picked = True
        with patch.object(StockPickingBranchReceipt, '_receive_interbranch_transfer', side_effect=UserError('Destino no disponible')):
            with self.assertRaisesRegex(UserError, 'Destino no disponible'):
                picking._action_done()
        self.assertNotEqual(picking.state, 'done')
        source_quants = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id), ('location_id', '=', self.source.lot_stock_id.id),
        ])
        self.assertEqual(sum(source_quants.mapped('quantity')), 5)

    def test_serial_identity_is_preserved_between_companies(self):
        self.product.tracking = 'serial'
        lot = self.env['stock.lot'].create({
            'name': 'SERIE-SRO-001', 'product_id': self.product.id, 'company_id': self.company.id,
        })
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 1, lot_id=lot)
        request = self._request(1)
        request.action_send_request()
        request.action_start_transfer()
        self._finish_outbound(request.picking_id, 1)
        receipt = request.picking_ids.filtered(lambda picking: picking.branch_direction == 'inbound')
        self.assertEqual(receipt.move_line_ids.lot_id.name, lot.name)
        self.assertEqual(receipt.move_line_ids.lot_id.company_id, self.destination_company)

    def test_multi_company_source_details_must_leave_source(self):
        self.env['stock.quant']._update_available_quantity(self.product, self.source.lot_stock_id, 1)
        request = self._request(1)
        request.action_send_request()
        request.action_start_transfer()
        picking = request.picking_id
        picking.action_assign()
        picking.action_approve_internal_transfer()
        picking.move_line_ids.location_dest_id = self.source.lot_stock_id
        with self.assertRaisesRegex(UserError, 'respetar las sucursales'):
            picking._check_operation_authorizations()
