# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..utils.points import format_points


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    tint_role = fields.Selection(
        selection=[
            ('base', "Base"),
            ('colorant', "Colorante"),
        ],
        string="Rol en entintado",
        help="Base: envase de pintura que recibe colorante. "
             "Colorante: tinte que se dispensa por puntos.")
    
    tint_schema_id = fields.Many2one(
        comodel_name='product.schema', string="Esquema de producto",
        )
    tint_line_id = fields.Many2one(
        comodel_name='lines.schema', string="Línea de esquema")

    # --- Solo para bases ------------------------------------------------
    tint_base_type_id = fields.Many2one(
        comodel_name='tint.base.type', string="Tipo de base",
        help="Determina cuánto colorante admite el envase.")
    tint_size_id = fields.Many2one(
        comodel_name='tint.size', string="Presentación")
    tint_capacity_points = fields.Integer(
        string="Colorante máximo (Pts.)", compute='_compute_tint_capacity',
        help="Se resuelve automáticamente desde la matriz de capacidad a "
             "partir del tipo de base y la presentación. No se captura.")
    tint_capacity_display = fields.Char(
        string="Colorante máximo", compute='_compute_tint_capacity',
        help="La misma capacidad en la notación de la operación, p. ej. 9Y 24.")

    tint_requires_extraction = fields.Boolean(
        string="Requiere extracción previa",
        related='tint_base_type_id.requires_extraction', readonly=True)
    tint_extraction_liters = fields.Float(
        string="Extraer antes de entintar (L)", digits=(12, 3),
        compute='_compute_tint_extraction_liters',
        help="Volumen que debe extraerse del envase antes de dispensar "
             "colorante. Se calcula sobre el volumen nominal de la presentación.")
    tint_operator_note = fields.Text(
        string="Instrucción al operador",
        related='tint_base_type_id.operator_note', readonly=True)

    # --- Solo para colorantes -------------------------------------------
    price_per_point = fields.Float(
        string="Precio por punto", digits='Product Price',
        help="Precio de venta de cada punto dispensado de este colorante.")

    # --- Cálculos -------------------------------------------------------

    @api.depends('tint_role', 'tint_base_type_id', 'tint_size_id')
    def _compute_tint_capacity(self):
        """Resuelve la capacidad desde la matriz.
        Es tolerante a propósito: devuelve cero si la combinación no existe,
        """
        for product in self:
            points = 0
            if product.tint_role == 'base' and product.tint_base_type_id and product.tint_size_id:
                points = product.tint_base_type_id.capacity_for(
                    product.tint_size_id, raise_if_missing=False)
            product.tint_capacity_points = points
            product.tint_capacity_display = format_points(points) if points else ""

    @api.depends('tint_role', 'tint_size_id', 'tint_base_type_id.requires_extraction',
                 'tint_base_type_id.extraction_percentage')
    def _compute_tint_extraction_liters(self):
        for product in self:
            liters = 0.0
            base_type = product.tint_base_type_id
            if (product.tint_role == 'base' and base_type.requires_extraction
                    and product.tint_size_id):
                liters = (
                    product.tint_size_id.volume_liters
                    * base_type.extraction_percentage / 100.0
                )
            product.tint_extraction_liters = liters

    # --- Validaciones ---------------------------------------------------

    @api.constrains('tint_role', 'tint_base_type_id', 'tint_size_id')
    def _check_tint_base(self):
        ### Detecta la mala configuración al capturar el catálogo.

        for product in self.filtered(lambda p: p.tint_role == 'base'):
            if not product.tint_base_type_id or not product.tint_size_id:
                raise ValidationError(_(
                    "La base «%s» necesita tipo de base y presentación para "
                    "poder resolver cuánto colorante admite.",
                    product.display_name,
                ))
            product.tint_base_type_id.capacity_for(product.tint_size_id)

    @api.constrains('tint_role', 'uom_id')
    def _check_colorant_uom(self):
        point = self.env.ref('entintados_pdv.uom_tint_point', raise_if_not_found=False)
        if not point:
            return
        for product in self.filtered(lambda p: p.tint_role == 'colorant'):
            if not product.uom_id or not product.uom_id._has_common_reference(point):
                raise ValidationError(_(
                    "El colorante «%(product)s» debe medirse en una unidad "
                    "compatible con el punto (punto u onza de colorante). "
                    "Actualmente usa «%(uom)s».",
                    product=product.display_name,
                    uom=product.uom_id.display_name or "",
                ))

    # --- Asistencia en el formulario ------------------------------------

    @api.onchange('tint_role')
    def _onchange_tint_role(self):
        ### Asigna la unidad del colorante y limpia los campos del otro rol.

        for product in self:
            if product.tint_role == 'colorant':
                point = self.env.ref(
                    'entintados_pdv.uom_tint_point', raise_if_not_found=False)
                if point:
                    product.uom_id = point
                product.tint_base_type_id = False
                product.tint_size_id = False
            elif product.tint_role == 'base':
                product.price_per_point = 0.0
            else:
                product.tint_base_type_id = False
                product.tint_size_id = False
                product.price_per_point = 0.0

    @api.onchange('tint_base_type_id')
    def _onchange_tint_base_type_id(self):
        """Avisa en el formulario si la combinación no está en la matriz."""
        self.ensure_one()
        if self.tint_role != 'base' or not self.tint_base_type_id or not self.tint_size_id:
            return
        if not self.tint_base_type_id.capacity_for(self.tint_size_id, raise_if_missing=False):
            return {
                'warning': {
                    'title': _("Combinación sin capacidad definida"),
                    'message': _(
                        "No hay capacidad de colorante registrada para la base "
                        "«%(base)s» en la presentación «%(size)s». Captúrela en "
                        "la matriz de capacidad antes de guardar este producto.",
                        base=self.tint_base_type_id.display_name,
                        size=self.tint_size_id.display_name,
                    ),
                }
            }

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        # EXTENDS point_of_sale: agrega los campos de entintado que la caja
        # necesita (rol, tipo y presentación de la base, capacidad y precio
        # por punto del colorante).
        field_names = super()._load_pos_data_fields(config)
        return field_names + [
            'tint_role', 'tint_base_type_id', 'tint_size_id',
            'tint_capacity_points', 'price_per_point',
        ]

    @api.model
    def _load_pos_data_search_read(self, data, config):
        # EXTENDS point_of_sale: garantiza que los colorantes lleguen al POS.
        #
        # Los colorantes son insumo del entintado (sale_ok=False,
        # available_in_pos=False), así que el dominio estándar los excluye y
        # el límite de productos podría dejarlos fuera. Se añaden aquí después
        # de la carga normal —igual que el core hace con el producto de
        # propina y los especiales— sin volverlos vendibles en caja. Al quedar
        # en data['product.template'], sus variantes product.product se cargan
        # solas por dependerse de esa lista.
        read = super()._load_pos_data_search_read(data, config)
        loaded_ids = {p['id'] for p in read}
        colorants = self.search([('tint_role', '=', 'colorant')])
        missing = colorants.filtered(lambda p: p.id not in loaded_ids)
        if missing:
            read += self._load_pos_data_read(missing, config)
        return read



