import json
from urllib.parse import urlencode

from odoo import api, fields, models, tools


class TintSalesReport(models.Model):
    _name = 'tint.sales.report'
    _description = 'Entintados vendidos'
    _auto = False
    _order = 'date_order desc, id desc'

    company_id = fields.Many2one('res.company', readonly=True)
    date_order = fields.Datetime(string='Fecha', readonly=True)
    source = fields.Selection([('sale', 'Ventas'), ('pos', 'Punto de venta')], string='Origen', readonly=True)
    reference = fields.Char(string='Documento', readonly=True)
    sale_order_id = fields.Many2one('sale.order', readonly=True)
    pos_order_id = fields.Many2one('pos.order', readonly=True)
    document_id = fields.Reference(
        [('sale.order', 'Venta'), ('pos.order', 'Venta de punto de venta')],
        string='Documento', compute='_compute_document')
    document_url = fields.Char(compute='_compute_document')
    base_id = fields.Many2one('product.product', string='Base', readonly=True)
    tint_category_id = fields.Many2one('tint.category', string='Categoría de entintado', readonly=True)
    color = fields.Char(string='Color', readonly=True)
    color_id = fields.Many2one('tint.color', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Cliente', readonly=True)
    salesperson_id = fields.Many2one('res.users', string='Vendedor', readonly=True)
    base_type_id = fields.Many2one('tint.base.type', string='Tipo de base', readonly=True)
    base_color = fields.Char(string='Base + color', compute='_compute_base_color')
    quantity = fields.Float(string='Entintados', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    invoiced_amount = fields.Monetary(string='Total', readonly=True, help='Venta con impuestos incluidos.')
    sale_price = fields.Monetary(string='Importe', readonly=True, help='Importe neto de la venta antes de impuestos.')
    margin = fields.Monetary(string='Margen', readonly=True)
    has_margin = fields.Boolean(readonly=True)
    invoiced = fields.Boolean(string='Facturado', readonly=True)
    status = fields.Selection([
        ('invoiced', 'Facturado'), ('pending', 'Pendiente'), ('quotation', 'Cotización'),
    ], string='Estado', readonly=True)
    pos_quotation = fields.Boolean(readonly=True)

    @api.depends('sale_order_id', 'pos_order_id')
    def _compute_document(self):
        for row in self:
            document = row.sale_order_id or row.pos_order_id
            row.document_id = document
            row.document_url = (
                f'{row.get_base_url()}/web#id={document.id}&model={document._name}&view_type=form'
                if document else False)

    def action_open_document(self):
        self.ensure_one()
        self.check_access('read')
        document = self.sale_order_id or self.pos_order_id
        document.check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'name': document.display_name,
            'res_model': document._name,
            'res_id': document.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _export_selection(self, extension):
        self.check_access('read')
        params = urlencode({
            'report_model': self._name,
            'domain': json.dumps([('id', 'in', self.ids)]),
            'companies': json.dumps(self.env.context.get('allowed_company_ids', [])),
            'show_margin': '1' if self.env.context.get('tint_show_margin') else '0',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/entintados_pdv/tint-sales/{extension}?{params}',
            'target': 'new' if extension == 'pdf' else 'download',
        }

    def action_export_excel(self):
        return self._export_selection('xlsx')

    def action_open_pdf(self):
        return self._export_selection('pdf')

    @api.depends('base_id.display_name', 'color')
    def _compute_base_color(self):
        for row in self:
            row.base_color = ' + '.join(filter(None, [row.base_id.display_name, row.color]))

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW tint_sales_report AS
            WITH pos_documents AS (
                SELECT id AS order_id, account_move AS move_id FROM pos_order WHERE account_move IS NOT NULL
                UNION
                SELECT order_id, move_id FROM pos_order_split_invoice_rel
            ), invoiced_pos AS (
                SELECT documents.order_id
                  FROM pos_documents documents
                  JOIN account_move invoice ON invoice.id = documents.move_id
                 WHERE invoice.move_type IN ('out_invoice', 'out_refund') AND invoice.state <> 'cancel'
                 GROUP BY documents.order_id
                HAVING BOOL_AND(invoice.state = 'posted')
            ), pos_invoice_amounts AS (
                SELECT documents.order_id, item.product_id,
                       SUM(CASE WHEN invoice.currency_id = COALESCE(journal.currency_id, company.currency_id)
                                THEN -item.amount_currency ELSE -item.balance * orders.currency_rate END) AS amount,
                       SUM((CASE WHEN invoice.move_type = 'out_refund' THEN -1 ELSE 1 END)
                           * item.price_total * CASE
                               WHEN invoice.currency_id = COALESCE(journal.currency_id, company.currency_id) THEN 1
                               ELSE orders.currency_rate / NULLIF(invoice.invoice_currency_rate, 0) END) AS total
                  FROM pos_documents documents
                  JOIN pos_order orders ON orders.id = documents.order_id
                  JOIN pos_config config ON config.id = orders.config_id
                  JOIN res_company company ON company.id = orders.company_id
             LEFT JOIN account_journal journal ON journal.id = config.journal_id
                  JOIN account_move invoice ON invoice.id = documents.move_id
                  JOIN account_move_line item ON item.move_id = invoice.id
                 WHERE invoice.state = 'posted' AND invoice.move_type IN ('out_invoice', 'out_refund')
                   AND item.display_type = 'product'
                 GROUP BY documents.order_id, item.product_id
            ), pos_product_lines AS (
                SELECT line.*, SUM(ABS(line.price_subtotal)) OVER product_lines AS product_subtotal,
                       SUM(ABS(line.qty)) OVER product_lines AS product_quantity
                  FROM pos_order_line line
                 WINDOW product_lines AS (PARTITION BY line.order_id, line.product_id)
            ), pos_tint_amounts AS (
                SELECT COALESCE(line.combo_parent_id, line.id) AS line_id,
                       SUM(line.price_subtotal) AS subtotal,
                       CASE WHEN BOOL_AND(line.is_total_cost_computed) THEN SUM(line.total_cost) END AS cost,
                       SUM(invoice.amount * COALESCE(
                           ABS(line.price_subtotal) / NULLIF(line.product_subtotal, 0),
                           ABS(line.qty) / NULLIF(line.product_quantity, 0))) AS invoiced_amount,
                       SUM(invoice.total * COALESCE(
                           ABS(line.price_subtotal) / NULLIF(line.product_subtotal, 0),
                           ABS(line.qty) / NULLIF(line.product_quantity, 0))) AS invoiced_total
                  FROM pos_product_lines line
             LEFT JOIN pos_invoice_amounts invoice
                    ON invoice.order_id = line.order_id AND invoice.product_id = line.product_id
                 GROUP BY COALESCE(line.combo_parent_id, line.id)
            ), posted_sale_qty AS (
                SELECT rel.order_line_id, SUM(
                    invoice_line.quantity * COALESCE(invoice_uom.factor / NULLIF(sale_uom.factor, 0), 1)
                ) AS quantity,
                SUM(CASE WHEN invoice.currency_id = orders.currency_id THEN invoice_line.price_subtotal
                         ELSE -invoice_line.balance * orders.currency_rate END) AS amount,
                SUM(invoice_line.price_total * CASE WHEN invoice.currency_id = orders.currency_id THEN 1
                    ELSE orders.currency_rate / NULLIF(invoice.invoice_currency_rate, 0) END) AS total
                  FROM sale_order_line_invoice_rel rel
                  JOIN account_move_line invoice_line ON invoice_line.id = rel.invoice_line_id
                  JOIN account_move invoice ON invoice.id = invoice_line.move_id
                  JOIN sale_order_line sale_line ON sale_line.id = rel.order_line_id
                  JOIN sale_order orders ON orders.id = sale_line.order_id
             LEFT JOIN uom_uom invoice_uom ON invoice_uom.id = invoice_line.product_uom_id
             LEFT JOIN uom_uom sale_uom ON sale_uom.id = sale_line.product_uom_id
                 WHERE invoice.state = 'posted' AND invoice.move_type = 'out_invoice'
                 GROUP BY rel.order_line_id
            ), posted_pos_sale_qty AS (
                SELECT line.sale_order_line_id, SUM(line.qty) AS quantity,
                       SUM(amounts.invoiced_amount * sale.currency_rate / NULLIF(orders.currency_rate, 0)) AS amount,
                       SUM(amounts.invoiced_total * sale.currency_rate / NULLIF(orders.currency_rate, 0)) AS total
                  FROM pos_order_line line
                  JOIN invoiced_pos invoiced ON invoiced.order_id = line.order_id
                  JOIN pos_order orders ON orders.id = line.order_id
                  JOIN pos_tint_amounts amounts ON amounts.line_id = line.id
                  JOIN sale_order_line sale_line ON sale_line.id = line.sale_order_line_id
                  JOIN sale_order sale ON sale.id = sale_line.order_id
                 WHERE line.sale_order_line_id IS NOT NULL AND line.qty > 0
                   AND orders.state IN ('paid', 'done', 'invoiced')
                 GROUP BY line.sale_order_line_id
            ), sale_tints AS (
                SELECT line.id, line.order_id, orders.company_id, orders.date_order, orders.name,
                       line.product_id, line.product_uom_qty, orders.state, orders.currency_id,
                       line.price_unit, line.price_subtotal,
                       (to_jsonb(line)->>'purchase_price')::numeric AS unit_cost,
                       COALESCE(NULLIF(line.tint_snapshot, 'false'::jsonb), header.tint_snapshot) AS recipe
                  FROM sale_order_line line
                  JOIN sale_order orders ON orders.id = line.order_id
             LEFT JOIN sale_order_line header ON header.id = line.tint_section_id
                 WHERE line.display_type IS NULL
            ), tint_bases AS (
                SELECT *, COALESCE(NULLIF(CONCAT_WS(' ',
                           NULLIF(recipe->>'color_code', ''), NULLIF(recipe->>'color_name', '')), ''),
                           recipe->>'name') AS color
                  FROM sale_tints
                 WHERE recipe->>'base_id' = product_id::text
            ), sale_quantities AS (
                SELECT tint.*, CASE WHEN tint.state = 'sale' THEN LEAST(tint.product_uom_qty,
                    GREATEST(COALESCE(sale.quantity, 0), COALESCE(pos.quantity, 0))) ELSE 0 END AS invoiced_qty,
                       CASE WHEN COALESCE(sale.quantity, 0) >= COALESCE(pos.quantity, 0)
                            THEN sale.amount ELSE pos.amount END AS invoiced_amount,
                       CASE WHEN COALESCE(sale.quantity, 0) >= COALESCE(pos.quantity, 0)
                            THEN sale.total ELSE pos.total END AS invoiced_total
                  FROM tint_bases tint
             LEFT JOIN posted_sale_qty sale ON sale.order_line_id = tint.id
             LEFT JOIN posted_pos_sale_qty pos ON pos.sale_order_line_id = tint.id
                 WHERE tint.state IN ('draft', 'sent', 'sale') AND tint.product_uom_qty > 0
            )
            SELECT tint.id * 4 + CASE WHEN bucket.invoiced THEN 2 ELSE 0 END AS id,
                   tint.company_id, tint.date_order,
                   'sale' AS source, tint.name AS reference, tint.product_id AS base_id,
                   tint.color, template.tint_base_type_id AS base_type_id,
                   bucket.quantity, bucket.invoiced,
                   CASE WHEN tint.state IN ('draft', 'sent') THEN 'quotation'
                        WHEN bucket.invoiced THEN 'invoiced' ELSE 'pending' END AS status,
                   tint.state IN ('draft', 'sent') AND (orders.pos_quotation_uuid IS NOT NULL OR EXISTS (
                       SELECT 1 FROM pos_order ticket WHERE ticket.generated_quotation_id = tint.order_id
                   )) AS pos_quotation,
                   tint.currency_id, revenue.amount AS sale_price,
                   CASE WHEN bucket.invoiced THEN tint.invoiced_total END AS invoiced_amount,
                   revenue.amount - tint.unit_cost * bucket.quantity AS margin,
                   tint.unit_cost IS NOT NULL AND revenue.amount IS NOT NULL AS has_margin,
                   tint.order_id AS sale_order_id, NULL::integer AS pos_order_id,
                   COALESCE(formula.color_id, catalog_color.id) AS color_id,
                   orders.partner_id, orders.user_id AS salesperson_id,
                   COALESCE(template.tint_category_id, formula.tint_category_id) AS tint_category_id
              FROM sale_quantities tint
              JOIN sale_order orders ON orders.id = tint.order_id
         LEFT JOIN tint_color_formula formula ON formula.id::text = tint.recipe->>'formula_id'
         LEFT JOIN tint_color catalog_color ON catalog_color.code = NULLIF(tint.recipe->>'color_code', '')
              JOIN product_product product ON product.id = tint.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
             CROSS JOIN LATERAL (VALUES (tint.invoiced_qty, TRUE),
                   (tint.product_uom_qty - tint.invoiced_qty, FALSE)) bucket(quantity, invoiced)
             CROSS JOIN LATERAL (SELECT CASE WHEN bucket.invoiced THEN tint.invoiced_amount
                    ELSE tint.price_subtotal * bucket.quantity / tint.product_uom_qty END AS amount) revenue
             WHERE bucket.quantity > 0
            UNION ALL
            SELECT line.id * 4 + 1, orders.company_id, orders.date_order,
                   'pos', orders.name, line.product_id,
                   COALESCE(linked.color, NULLIF(CONCAT_WS(' ',
                       CASE WHEN STRPOS(COALESCE(line.customer_note, ''), line.tint_color_code) = 0
                            THEN NULLIF(line.tint_color_code, '') END,
                       NULLIF(regexp_replace(line.customer_note, '^Color:[[:space:]]*', ''), '')
                   ), ''), 'Sin identificar'),
                   template.tint_base_type_id, line.qty, invoiced.order_id IS NOT NULL,
                   CASE WHEN invoiced.order_id IS NOT NULL THEN 'invoiced' ELSE 'pending' END,
                   FALSE, COALESCE(journal.currency_id, company.currency_id),
                   CASE WHEN invoiced.order_id IS NOT NULL THEN amounts.invoiced_amount ELSE amounts.subtotal END,
                   CASE WHEN invoiced.order_id IS NOT NULL THEN amounts.invoiced_total END,
                   (CASE WHEN invoiced.order_id IS NOT NULL THEN amounts.invoiced_amount
                         ELSE amounts.subtotal END) - amounts.cost,
                   amounts.cost IS NOT NULL AND (invoiced.order_id IS NULL OR amounts.invoiced_amount IS NOT NULL),
                   NULL::integer, orders.id, COALESCE(linked_formula.color_id, catalog_color.id),
                   orders.partner_id, orders.user_id,
                   COALESCE(template.tint_category_id, linked_formula.tint_category_id)
              FROM pos_order_line line
              JOIN pos_order orders ON orders.id = line.order_id
              JOIN pos_tint_amounts amounts ON amounts.line_id = line.id
              JOIN pos_config config ON config.id = orders.config_id
              JOIN res_company company ON company.id = orders.company_id
         LEFT JOIN account_journal journal ON journal.id = config.journal_id
              JOIN product_product product ON product.id = line.product_id
              JOIN product_template template ON template.id = product.product_tmpl_id
         LEFT JOIN tint_bases linked ON linked.id = line.sale_order_line_id
         LEFT JOIN tint_color_formula linked_formula ON linked_formula.id::text = linked.recipe->>'formula_id'
         LEFT JOIN tint_color catalog_color ON catalog_color.code = COALESCE(
                   NULLIF(line.tint_color_code, ''), NULLIF(linked.recipe->>'color_code', ''))
         LEFT JOIN invoiced_pos invoiced ON invoiced.order_id = orders.id
             WHERE orders.state IN ('paid', 'done', 'invoiced')
               AND line.combo_parent_id IS NULL AND line.qty <> 0
               AND (linked.id IS NOT NULL OR NULLIF(line.tint_color_code, '') IS NOT NULL
                    OR (template.tint_role = 'base' AND EXISTS (
                        SELECT 1 FROM pos_order_line component
                         WHERE component.combo_parent_id = line.id
                    )))
               AND (line.qty < 0 OR linked.state IS DISTINCT FROM 'sale')
        """)


class TintCategorySalesReport(models.Model):
    _name = 'tint.category.sales.report'
    _inherit = 'tint.sales.report'
    _description = 'Ventas de entintados por categoría'
    _auto = False

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW tint_category_sales_report AS
            SELECT * FROM tint_sales_report
             WHERE tint_category_id IS NOT NULL
               AND NULLIF(BTRIM(color), '') IS NOT NULL
               AND color <> 'Sin identificar'
               AND status IN ('pending', 'invoiced')
        """)
