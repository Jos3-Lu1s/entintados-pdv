# Anticipos, crédito disponible y riesgo financiero

## Integración utilizada

El cálculo de riesgo financiero ya está incorporado en este módulo, en `models/res_partner_risk.py` y `models/account_invoice_risk.py`. Esta implementación lo extiende; no instala otra copia del riesgo financiero ni depende de `purchase_advance_payment`. No se encontró ese addon independiente en el proyecto revisado.

La integración se activa por compañía. Actualizar el módulo agrega las funciones, pero no crea cuentas en la base de trabajo ni cambia su configuración automáticamente.

## Configuración

1. Actualizar `entintados_pdv` a `19.0.1.0.4` y reiniciar Odoo.
2. En Contabilidad → Configuración → Ajustes → Anticipos y riesgo financiero, seleccionar:
   - Cuenta de anticipos de clientes: pasivo circulante, conciliable.
   - Cuenta de anticipos a proveedores: activo circulante, conciliable.
   - Producto de anticipo: servicio; cuenta de ingreso = anticipos de clientes, cuenta de gasto = anticipos a proveedores.
3. Puede usarse **Preparar cuentas y producto de anticipos**, con permisos de administrador contable. Crea `ANTCLI`, `ANTPRO` y el servicio `ANTICIPO-{compañía}` cuando aún no están configurados. Si esos códigos ya existen, se solicita elegir sus cuentas; no se reutilizan automáticamente cuentas de otro tipo. Ajustar los códigos al catálogo contable de la empresa si procede.
4. Activar **Integrar anticipos con riesgo financiero**. Guardar Ajustes.
5. Configurar los impuestos del producto/comprobante conforme a la operación. El botón de preparación deja el producto sin impuestos para no suponer una tasa de IVA.
6. Revisar los métodos de pago de los diarios de caja y bancos. Los anticipos pagados utilizan los asientos y la conciliación de pagos de Odoo; no se fuerza el estado de conciliación bancaria.
7. Recargar el POS y verificar que el producto de anticipo esté disponible en la caja. Se requiere conexión para consultar saldo actualizado.
8. Revisar las categorías incluidas en el riesgo de cada cliente. Si el POS contabiliza en una cuenta por cobrar distinta de la cuenta del contacto, incluir los otros importes abiertos y vencidos para que el saldo contabilizado de caja participe en el límite. Configurar también las claves SAT y la unidad del producto cuando se vaya a emitir un CFDI.

## Clientes desde Ventas

Se utiliza el asistente nativo de Odoo: pedido confirmado → Crear factura → Anticipo por porcentaje o importe fijo. La cuenta nativa `downpayment_account_id` es la cuenta de pasivo configurada arriba.

La factura de anticipo no constituye saldo pagado por sí sola. Después de contabilizarla y registrar su pago, el importe conciliado queda disponible. La factura final nativa conserva la deducción de anticipo y se vincula con la factura original. No puede contabilizar una deducción superior al importe pagado disponible.

No se retocan automáticamente anticipos históricos contabilizados en cuentas de ingresos. Para incluirlos en este flujo, se requiere revisar su cuenta y sus aplicaciones antes de activarlo.

## Proveedores desde Compras

Desde una orden confirmada, **Factura de anticipo** prepara un borrador del comprobante recibido del proveedor, por importe fijo antes de impuestos o porcentaje del subtotal. Los impuestos se seleccionan según ese comprobante. La factura está vinculada a la compra, pero no marca las mercancías como recibidas ni facturadas.

Debe revisarse/importarse el comprobante recibido y contabilizarlo. Su pago se registra con el flujo normal de Odoo. El saldo pagado aparece como anticipo disponible del proveedor. No se emite ni timbra un CFDI a nombre del proveedor.

Si todavía no se ha recibido un comprobante, este botón no sustituye un CFDI: conserva el borrador pendiente. El flujo de pagos independientes de un addon `purchase_advance_payment` no se instala ni se modifica aquí.

## Aplicar a la factura posterior

El botón **Aplicar anticipo** ofrece dos procedimientos; elegir el que corresponde a los documentos de la operación:

| Procedimiento | Documentos y resultado |
|---|---|
| Factura por remanente | La factura final debe estar en borrador. Se agrega una deducción proporcional en la cuenta del anticipo y se reproducen sus impuestos. La factura final queda por el importe restante. |
| Factura completa y nota de aplicación | La factura final ya está contabilizada por el total. Se prepara una nota en borrador usando la cuenta e impuestos del anticipo. Al contabilizarla, Odoo concilia la cuenta por cobrar/pagar con la factura final. |

La aplicación copia los impuestos del anticipo en proporción al importe solicitado; si los redondeos no dan exactamente el importe, exige ajustar el importe. No modifica la factura completa contabilizada.

La nota de aplicación se mantiene diferenciada de las notas de crédito comerciales agrupadas por descuento. Ambas pueden coexistir, pero no se genera una nota comercial para descontar nuevamente un anticipo.

Los documentos muestran un botón inteligente y una pestaña **Anticipos** con las facturas originales, aplicaciones, importes pagados, aplicados, reservados y disponibles.

## Contabilidad: ejemplo sin impuestos

Anticipo de $3,000 y operación final de $10,000:

| Momento | Cliente | Proveedor |
|---|---|---|
| Factura de anticipo | Cargo a clientes $3,000; abono a anticipos de clientes $3,000 | Cargo a anticipos a proveedores $3,000; abono a proveedores $3,000 |
| Pago/cobro | Cargo a banco/cuenta de cobros pendientes; abono a clientes | Cargo a proveedores; abono a banco/cuenta de pagos pendientes |
| Factura por remanente | Cargo a clientes $7,000 y anticipos $3,000; abono a ingreso $10,000 | Cargo a compra/gasto $10,000; abono a anticipos $3,000 y proveedores $7,000 |

En el otro procedimiento, se contabiliza la factura completa y una nota de aplicación separada que cancela el saldo del anticipo y liquida $3,000 de la cuenta por cobrar/pagar. El saldo posterior sigue siendo $7,000. Cuando hay impuestos, los importes de cuenta de anticipo son antes de impuestos y el disponible mostrado es con impuestos; Odoo contabiliza el impuesto por separado.

## Caja y bancos

### Tres modalidades del POS

| Modalidad | Operación | Dinero recibido | Resultado |
|---|---|---|---|
| Pago inicial + saldo PPD | Venta de $1,000; cobro inicial de $300 | $300 | Factura completa de $1,000 con $700 pendientes |
| Crédito autorizado | Venta de $1,000 financiada al cliente | $0 si se financia todo | Cuenta por cobrar de $1,000 sujeta a autorización y límite |
| Saldo a favor | Cliente entrega $300 antes de elegir una compra | $300 | Anticipo disponible de $300 para una venta posterior |

En la pantalla de pago, primero capturar el importe real que se recibe usando efectivo, tarjeta o banco. Después pulsar **Pago inicial + saldo PPD**: la diferencia se agrega con el método nativo `pay_later`, que no representa efectivo. Esta modalidad exige un pago inicial positivo y permite al cliente dejar saldo sin tener crédito autorizado, según la política solicitada. No modifica su límite ni crea un saldo a favor.

**Crédito autorizado** puede financiar el total o la parte restante. Exige la marca de cliente a crédito, un límite positivo y disponibilidad suficiente. La comprobación ocurre en servidor y toma el mayor de la exposición calculada por riesgo y la cuenta por cobrar contable neta, evitando omitir deuda porque alguna categoría opcional esté desactivada. Los anticipos realmente disponibles se consideran una sola vez.

Ambas modalidades requieren cliente y factura. El servidor fija `l10n_mx_edi_payment_policy = PPD` y un término de pago configurable. PPD es la política fiscal; el término determina el vencimiento. El término predeterminado **PPD - saldo pendiente** no concede días adicionales; el administrador puede sustituirlo por el plazo acordado en la configuración de la caja. **Cambiar a pago completo** retira la línea de deuda y conserva los pagos reales capturados.

En la configuración del POS, **Preparar saldo pendiente y crédito** habilita/reutiliza un método nativo sin diario bancario (`pay_later`) y configura el término PPD si aún no existe. No asigna crédito ni aumenta límites de clientes.

Cuando el cliente regresa, abrir un ticket vacío, seleccionarlo y usar **Más → Cobrar saldo pendiente**. Se utiliza la cobranza nativa de `pos_settle_due`: elegir su factura, cobrar por un medio real y validar. No genera otra venta de mercancía. La disponibilidad para cobrar en caja se actualiza con la operación; la conciliación contable de estos cobros se completa al cerrar la sesión, conforme al flujo nativo. También puede registrarse el pago directamente desde Contabilidad contra la factura.

**Saldo a favor → Registrar anticipo del cliente** sigue siendo el flujo de anticipo pagado descrito abajo. El crédito concedido por riesgo financiero es una autorización de financiación; no se convierte en un anticipo pagado. Los pagos PPD requieren revisar los complementos de pago del flujo mexicano; no se probó timbrado real con PAC. Referencia: [Localización mexicana de Odoo 19](https://www.odoo.com/documentation/19.0/es_419/applications/finance/fiscal_localizations/mexico.html).

### Anticipo nativo de un pedido de Ventas

El módulo depende de `pos_sale`, la conexión nativa entre Ventas y POS. En caja, **Anticipo de pedido** abre el selector de cotizaciones/pedidos de Odoo. Desde un ticket vacío, elegir el pedido y después **Anticipo por porcentaje** o **Anticipo por importe fijo**. El porcentaje se calcula sobre el saldo pendiente del pedido. Cobrar con el medio de pago real de caja.

Configurar el **Producto de anticipo** nativo del Punto de Venta (`down_payment_product_id`). Al activar la integración contable, estos anticipos requieren factura y usan la cuenta de pasivo de anticipos de clientes, aun cuando el producto nativo sea distinto del producto de anticipos generales. Se conservan los impuestos calculados por Odoo para el pedido.

Odoo agrega el anticipo al pedido y permite después liquidarlo desde **Cotización/Pedido** o generar la factura final desde Ventas. La deducción queda vinculada a la factura de anticipo y consume su saldo disponible una sola vez. Los anticipos del pedido y el saldo general del cliente comparten el mismo control; si un saldo ya se aplicó a otra operación, se debe corregir esa aplicación antes de volver a descontarlo del pedido.

El acceso nativo puede utilizarse sin activar la integración con riesgo. En ese caso conserva el comportamiento estándar de `pos_sale` y su configuración de cuentas. Las reglas de factura obligatoria y la cuenta de pasivo descritas arriba se aplican al activar la integración por compañía.

### Anticipo general de cliente

En POS, **Anticipos** permite consultar saldo y disponible para comprar, cobrar un anticipo en un pedido vacío o aplicarlo a una venta antes de registrar pagos. Se exige cliente y factura.

- El cobro de $3,000 ingresa a caja una sola vez, mediante el pago normal de la factura de anticipo.
- Aplicar esos $3,000 a una venta de $10,000 deja $7,000 a cobrar. La aplicación no agrega un pago ficticio de caja ni un movimiento bancario nuevo.
- El cierre de sesión y la conciliación bancaria siguen los procesos de Odoo. Un pago registrado desde Contabilidad contra un diario de caja no se incorpora artificialmente como cobro a una sesión POS.
- Los anticipos no admiten descuentos, facturas divididas ni redondeo de efectivo en este flujo POS. No pueden cobrarse usando «Pagar después».
- Los anticipos clasificados fiscalmente se aplican desde Contabilidad, donde se revisan sus CFDI; el POS aplica los pagos a cuenta de operaciones definidas.

## Riesgo y crédito

El límite autorizado de crédito no cambia.

`Disponible del anticipo = pagado − aplicado en documentos contabilizados − reservado en borradores`.

`Riesgo neto = máximo(riesgo bruto − anticipos de cliente disponibles, 0)`.

`Disponible para comprar = máximo(límite − riesgo bruto + anticipos disponibles, 0)`.

Ejemplo: límite $10,000, riesgo bruto $6,000 y anticipos disponibles $3,000 → riesgo neto $3,000 y disponible para comprar $7,000. Una aplicación convierte ese anticipo en deducción de la factura; deja de estar disponible y no se resta otra vez del riesgo.

Los límites específicos y el control de vencidos se conservan: tener un anticipo no concilia automáticamente una deuda vencida. Debe aplicarse al documento que corresponda. Un anticipo a proveedor no aumenta el crédito del mismo contacto cuando también es cliente.

Las ventas POS a crédito no facturadas y aún en sesiones abiertas se incluyen mientras no estén reflejadas en contabilidad. Al facturarlas o cerrar la sesión se utiliza su saldo contable, evitando duplicarlo. Las ventas a crédito en POS se validan en servidor; una actualización visual del saldo no permite superar el límite por sí sola.

## Fiscalidad mexicana

«Anticipo» operativo no siempre significa anticipo fiscal. Una parcialidad de una compra/venta cuyos bienes y precio ya se conocen se registra como **pago a cuenta de una operación definida**. La opción **anticipo fiscal** corresponde al procedimiento documental de anticipo cuando aún no están determinados el bien/servicio o su precio.

Para el procedimiento fiscal, las facturas por remanente relacionan los UUID de los anticipos mediante el campo nativo `l10n_mx_edi_cfdi_origin` de Odoo con tipo `07`. La nota de aplicación de una factura completa relaciona el UUID de esa factura. Se exige la localización mexicana y los UUID de origen. No se timbra automáticamente ni se cambia silenciosamente una relación fiscal de otro tipo.

Para proveedores, se registran los CFDI emitidos por ellos; preparar un borrador o asignar una relación interna no modifica el XML firmado recibido. Revisar que los documentos recibidos correspondan al procedimiento elegido y que el importe/taxes del borrador coincidan con el XML antes de contabilizar.

Referencias: [Guía SAT de anticipos](https://www.sat.gob.mx/minisitio/Factura/documentos/Guia_llenadoCFDI_DPA.pdf), [Pagos Odoo 19](https://www.odoo.com/documentation/19.0/applications/finance/accounting/payments.html), [Anticipos en Ventas Odoo 19](https://www.odoo.com/documentation/19.0/applications/sales/sales/invoicing/down_payment.html).

## Cancelaciones

Cancelar una aplicación libera su saldo; restablecerla a borrador lo vuelve a reservar. No borra ni duplica el pago original. No se puede restablecer una factura de anticipo ni retirar su conciliación de pago mientras tenga aplicaciones/reservas que excederían el saldo financiado. Primero deben cancelarse esas aplicaciones.

La devolución de un anticipo se tramita con el documento de devolución y su pago desde Contabilidad. La cancelación contable no sustituye la cancelación fiscal ante el SAT.

## Alcance de la validación

La comprobación se realiza en una base aislada de Odoo 19 y con pruebas de los controles JavaScript del POS. Incluye anticipos parcialmente pagados, aplicaciones históricas de Ventas, reservas en borradores, impuestos, separación cliente/proveedor, riesgo financiero, rechazo de una operación POS cuya factura falla y notas de crédito individuales con conciliación y cancelación.

Estas pruebas no incluyen un timbrado real con PAC ni un recorrido visual completo de apertura, cobro y cierre de una sesión POS. La base de trabajo conserva su configuración: es necesario actualizar el módulo y configurar/activar la integración por compañía antes de utilizarla.
