from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import re
from urllib.parse import urlsplit, parse_qs
import json
from types import SimpleNamespace
from unittest.mock import patch

from lxml import html as lxml_html

from odoo import Command
from odoo.http import Response
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.convert import convert_file

from .test_vendor_credit_group import TestVendorCreditGroup
from ..controllers import distributed_credit_report as report_controller


@tagged('post_install', '-at_install')
class TestDistributedCreditReport(TransactionCase):
    _invoice = TestVendorCreditGroup._invoice
    _group = TestVendorCreditGroup._group

    def _render_offline_pdf(self, renderer, ids, data=None):
        """Inline assets created in this test transaction, invisible to HTTP workers."""
        native = renderer._run_wkhtmltopdf

        def inline(source):
            if not source:
                return source
            root = lxml_html.fromstring(source)
            for link in root.xpath('//link[@href]'):
                asset = self.env['ir.attachment'].search([('url', '=', urlsplit(link.get('href')).path)], limit=1)
                if asset and asset.raw:
                    style = lxml_html.Element('style')
                    style.text = re.sub(r'url\([^)]*\)', 'none', asset.raw.decode('utf-8'))
                    link.addprevious(style)
                link.drop_tree()
            for node in root.xpath('//script[@src] | //base'):
                node.drop_tree()
            for image in root.xpath('//img[@src]'):
                if not image.get('src').startswith('data:'):
                    image.drop_tree()
            return lxml_html.tostring(root, encoding='unicode')

        def offline(bodies, *args, **kwargs):
            for key in ('header', 'footer'):
                if key in kwargs:
                    kwargs[key] = inline(kwargs[key])
            return native([inline(body) for body in bodies], *args, **kwargs)

        with patch.object(type(renderer), '_run_wkhtmltopdf', side_effect=offline):
            return renderer._render_qweb_pdf(renderer.report_name, ids, data=data)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.payable = cls.env['account.account'].create({'name': 'Reporte por pagar QA', 'code': 'RDPAY',
            'account_type': 'liability_payable', 'reconcile': True, 'company_ids': [Command.set(cls.company.ids)]})
        cls.expense = cls.env['account.account'].create({'name': 'Reporte gasto QA', 'code': 'RDEXP',
            'account_type': 'expense', 'company_ids': [Command.set(cls.company.ids)]})
        cls.partner = cls.env['res.partner'].create({'name': 'Proveedor de pinturas y recubrimientos para reporte de notas distribuidas QA',
            'is_supplier': True, 'distributed_notes': True, 'property_account_payable_id': cls.payable.id})
        cls.journal = cls.env['account.journal'].create({'name': 'Compras reporte QA', 'code': 'RDC', 'type': 'purchase'})
        cls.payment_journal = cls.env['account.journal'].create({'name': 'Pagos reporte QA', 'code': 'RDP', 'type': 'general'})

    def test_generated_totals_and_source_invoices(self):
        group = self._group(self._invoice() | self._invoice())
        group.action_create_credit()
        values = self.env['report.entintados_pdv.distributed_credit_report']._get_report_values(group.ids)
        section = values['sections'][0]
        self.assertEqual(section['posted'], 120)
        self.assertEqual(section['requested'], 120)
        self.assertEqual(section['count'], 2)
        self.assertEqual(len(section['items'][0]['rows']), 2)

    def test_preview_opens_native_pdf_in_another_tab(self):
        group = self._group(self._invoice())
        action = group.action_preview_distributed_credit()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['url'], '/entintados_pdv/distributed-credits/pdf/%s/%s.pdf' % (group.id, group.name))
        general = group.action_preview_all_distributed_credits()
        self.assertEqual(general['target'], 'new')
        options = json.loads(parse_qs(urlsplit(general['url']).query)['options'][0])
        values = self.env['report.entintados_pdv.distributed_credit_report']._get_report_values(None, options)
        self.assertTrue(values['summary_only'])
        self.assertIn(group, values['docs'])

    def test_individual_pdf_uses_folio_as_inline_filename(self):
        group = self._group(self._invoice())
        fake_request = SimpleNamespace(env=self.env, make_response=lambda content, headers: Response(content, headers=headers))
        with patch.object(report_controller, 'request', fake_request), patch.object(
                type(self.env['ir.actions.report']), '_render_qweb_pdf', return_value=(b'%PDF-test', 'pdf')):
            response = report_controller.DistributedCreditExport().distributed_credit_pdf(
                group.id, 'old-filename.pdf')
        self.assertEqual(response.data, b'%PDF-test')
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'))
        self.assertIn(group.name + '.pdf', response.headers['Content-Disposition'])

    def test_excel_matches_report_and_has_landscape_sheets(self):
        group = self._group(self._invoice() | self._invoice())
        group.action_create_credit()
        content = self.env['report.entintados_pdv.distributed_credit_report']._build_xlsx(group.ids)
        Path('/tmp/distributed_credit_qa.xlsx').write_bytes(content)
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(BytesIO(content)) as archive:
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
            self.assertEqual([sheet.get('name') for sheet in workbook.findall('x:sheets/x:sheet', ns)],
                             ['General', 'Distribucion', 'Notas generadas'])
            for name in ('sheet1.xml', 'sheet2.xml', 'sheet3.xml'):
                sheet = ET.fromstring(archive.read('xl/worksheets/' + name))
                self.assertEqual(sheet.find('x:pageSetup', ns).get('orientation'), 'landscape')
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            formulas = sheet.findall('.//x:c[x:f]', ns)
            self.assertEqual(len(formulas), 2)
            self.assertEqual([float(cell.find('x:v', ns).text) for cell in formulas], [120, 120])

    def test_excel_actions_are_available_in_gear_menu(self):
        convert_file(self.env, 'entintados_pdv', 'views/distributed_credit_export_actions.xml', {}, mode='update')
        group = self._group(self._invoice())
        action = self.env.ref('entintados_pdv.action_distributed_credit_excel_selected').with_context(
            active_model='vendor.credit.group', active_ids=group.ids).run()
        self.assertIn('ids=%s' % group.id, action['url'])
        self.assertEqual(self.env.ref('entintados_pdv.action_distributed_credit_excel_all').binding_type, 'action')
        all_action = group.action_export_distributed_credits_xlsx(all_records=True)
        self.assertTrue(all_action['url'].endswith('all=1'))

    def test_excel_treats_reason_as_text(self):
        group = self._group(self._invoice())
        group.reason = '=1+1'
        content = self.env['report.entintados_pdv.distributed_credit_report']._build_xlsx(group.ids)
        with ZipFile(BytesIO(content)) as archive:
            strings = archive.read('xl/sharedStrings.xml')
            self.assertIn(b'=1+1', strings)
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            self.assertEqual(len(sheet.findall('.//x:f', ns)), 2)

    def test_general_action_keeps_doc_ids_in_download_data(self):
        group = self._group(self._invoice())
        action = group.with_context(discard_logo_check=True).action_download_all_distributed_credits()
        self.assertTrue(action['data']['summary_only'])
        self.assertIn(group.id, action['data']['doc_ids'])
        values = self.env['report.entintados_pdv.distributed_credit_report']._get_report_values(None, action['data'])
        self.assertIn(group, values['docs'])

    def test_cancelled_credit_does_not_count_as_posted(self):
        group = self._group(self._invoice())
        group.action_create_credit()
        group.action_cancel()
        section = self.env['report.entintados_pdv.distributed_credit_report']._get_report_values(group.ids)['sections'][0]
        self.assertEqual(section['posted'], 0)
        self.assertEqual(section['count'], 0)

    def test_renders_individual_and_general_downloads(self):
        convert_file(self.env, 'entintados_pdv', 'report/distributed_credit_report.xml', {}, mode='update')
        self.company.name = 'Comercializadora de Pinturas y Recubrimientos - Compañía de Prueba'
        self.company.partner_id.street = 'Avenida de los Recubrimientos 123, edificio de administración'
        self.company.partner_id.street2 = 'Colonia Centro, oficina de atención a proveedores'
        self.company.report_header = 'HEADER_FROM_EXTERNAL_LAYOUT_MUST_NOT_OVERLAP'
        group = self._group(self._invoice() | self._invoice())
        group.action_create_credit()
        report = self.env.ref('entintados_pdv.action_report_distributed_credit')
        html, _ = report._render_qweb_html(report.report_name, group.ids)
        self.assertIn(b'Distribuci', html)
        self.assertIn(group.credit_ids[0].name.encode(), html)
        self.assertIn(b'credit-brand', html)
        self.assertNotIn(b'HEADER_FROM_EXTERNAL_LAYOUT_MUST_NOT_OVERLAP', html)
        renderer = report.with_context(force_report_rendering=True)
        pdf, _ = self._render_offline_pdf(renderer, group.ids)
        self.assertTrue(pdf.startswith(b'%PDF'))
        Path('/tmp/distributed_credit_individual_qa.pdf').write_bytes(pdf)
        groups = group
        for _ in range(25):
            groups |= self._group(self._invoice())
        pdf, _ = self._render_offline_pdf(renderer, groups.ids,
            data={'summary_only': True, 'doc_ids': groups.ids})
        self.assertTrue(pdf.startswith(b'%PDF'))
        Path('/tmp/distributed_credit_general_qa.pdf').write_bytes(pdf)
