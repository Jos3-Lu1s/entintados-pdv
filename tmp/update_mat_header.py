from pathlib import Path
from lxml import etree

path = Path('report/invoice_mat.xml')
tree = etree.parse(str(path))
root = tree.getroot()
layout = root.find("template[@id='invoice_mat_layout']")
layout.find('t').set('t-if', "o._name != 'sale.order' and o.move_type != 'out_invoice'")
before = root.find("template[@id='report_invoice_mat']").find("xpath[@expr=\"//div[@id='informations']\"]")
for child in list(before):
    before.remove(child)
etree.SubElement(before, 't', {'t-if': "o.move_type == 'out_invoice'", 't-call': 'entintados_pdv.mat_document_header'})
header = etree.fromstring('''<template id="mat_document_header">
    <t t-set="mat_header" t-value="o._mat_report_header()"/>
    <t t-set="mat_partner" t-value="mat_header['partner']"/>
    <t t-set="mat_agent" t-value="mat_header['agent']"/>
    <table class="mat-top"><tr>
        <td style="width:34%;"><div class="mat-panel mat-brand">
            <img src="/entintados_pdv/static/src/img/invoice_logo_placeholder.png" style="width:210px;height:80px;object-fit:contain;" alt="Logotipo de la empresa"/>
            <div class="mat-folio"><strong t-out="mat_header['title'] + ':'"/> <span t-out="o.name if o.name and o.name != '/' else 'Borrador'"/></div>
            <div class="mat-stage">Etapa: <strong t-out="mat_header['stage']"/></div>
        </div></td>
        <td style="width:36%;"><div class="mat-company" style="padding:14px 8px;">
            <strong t-field="o.company_id.name"/><br/>
            <strong>RFC: <span t-field="o.company_id.vat"/></strong>
            <div t-field="o.company_id.partner_id" t-options="{'widget':'contact','fields':['address'],'no_marker':True}"/>
            <span t-field="o.company_id.website"/>
        </div></td>
        <td style="width:30%;"><div class="mat-panel mat-dates">
            <div class="mat-heading" t-out="mat_header['date_label']"/><div class="mat-date" t-out="mat_header['date']"/>
        </div><div class="mat-panel mat-dates" style="margin-top:10px;">
            <div class="mat-heading" t-out="mat_header['due_label']"/><div class="mat-date" t-out="mat_header['due'] or 'Sin fecha definida'"/>
        </div><div style="text-align:center;font-size:11px;margin-top:5px;">Moneda: <span t-field="o.currency_id.name"/></div></td>
    </tr></table>
    <table class="mat-top"><tr>
        <td style="width:70%;"><div class="mat-panel">
            <div class="mat-heading">En atención a:</div><div style="padding:12px;line-height:1.5;">
                <table class="mat-details"><tr><th>CLAVE:</th><td t-field="mat_partner.ref"/></tr>
                <tr><th>NOMBRE:</th><td t-field="mat_partner.name"/></tr>
                <tr><th>RFC:</th><td t-out="mat_header['rfc']"/></tr>
                <tr><th>AGENTE:</th><td t-field="mat_agent.name"/></tr>
                <tr><th>Dirección:</th><td><div t-field="mat_partner" t-options="{'widget':'contact','fields':['address'],'no_marker':True}"/></td></tr>
                <tr t-if="o.partner_shipping_id and o.partner_shipping_id != mat_partner"><th>Entrega a:</th><td><div t-field="o.partner_shipping_id" t-options="{'widget':'contact','fields':['name','address'],'no_marker':True}"/></td></tr>
                <tr t-if="mat_header['origin']"><th>Origen:</th><td t-out="mat_header['origin']"/></tr></table>
                <t t-if="not mat_header['sale']" t-call="l10n_mx_edi.l10n_mx_fiscal_regime_receptor"/>
            </div>
        </div></td>
        <td style="width:30%;"><div class="mat-panel" style="padding:12px;line-height:1.5;">
            <strong t-field="mat_agent.name"/><div style="font-size:11px;margin-top:14px;">
                <div t-if="mat_agent.partner_id.phone"><strong>Teléfono: </strong><span t-field="mat_agent.partner_id.phone"/></div>
                <t t-set="mat_agent_address" t-value="mat_agent.partner_id if mat_agent.partner_id.street else o.company_id.partner_id"/>
                <div t-field="mat_agent_address" t-options="{'widget':'contact','fields':['address'],'no_marker':True}"/>
                <div t-if="mat_agent.email"><span t-field="mat_agent.email"/></div>
                <div t-if="mat_header['reference']"><strong>Referencia: </strong><span t-out="mat_header['reference']"/></div>
            </div>
        </div></td>
    </tr></table>
    <div class="mat-panel" style="padding:8px 12px;font-size:10px;margin:4px 7px 12px;">
        <strong>Uso CFDI: </strong><span t-out="mat_header['usage']"/>
        <br/><strong>Forma de pago: </strong><span t-out="mat_header['payment_way']"/>
        <strong> · Método de pago: </strong><span t-out="mat_header['payment_method']"/>
        <t t-if="mat_header['bank_account']"><br/>Cuenta bancaria: <span t-out="mat_header['bank_account']"/></t>
    </div>
</template>''')
root.insert(0, header)
style = layout.find('.//style')
style.text += '''
    .mat-invoice .mat-panel {background:white;border:1px solid #f2f2f2;border-radius:12px;box-shadow:0 2px 6px #ededed;overflow:hidden;}
    .mat-invoice table.mat-top {border:0!important;border-spacing:6px;margin:0 0 4px;}
    .mat-invoice .mat-top > tbody > tr > td,.mat-invoice .mat-top > tr > td {padding:0!important;border:0!important;vertical-align:top;}
    .mat-invoice .mat-company {font-size:12px;line-height:1.45;}
    .mat-invoice .mat-brand img {margin:8px auto;}
    .mat-invoice .mat-folio {padding:8px;background:#efeddb;color:#24468d;}
    .mat-invoice .mat-stage {font-size:10px;padding:5px;color:#24468d;}
    .mat-invoice .mat-date {padding:9px;font-size:12px;}
    .mat-invoice .mat-details {border:0;font-size:12px;}
    .mat-invoice .mat-details th {width:85px;text-align:left;vertical-align:top;padding:0 8px 0 0;}
    .mat-invoice .mat-details td {text-align:left;padding:0;}
    .mat-invoice .mat-lines {border-spacing:0;box-shadow:0 2px 6px #ededed;}
'''
path.write_bytes(etree.tostring(root, pretty_print=True, encoding='utf-8', xml_declaration=True))
