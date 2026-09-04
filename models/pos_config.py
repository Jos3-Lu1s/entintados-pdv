# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    tint_default_gallery_id = fields.Many2one(
        'tint.gallery',
        string="Galería de entintado por defecto",
        domain=[('active', '=', True)],
        help="Galería asignada automáticamente al registrar nuevos colores y fórmulas desde este Punto de Venta.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        if fields and 'tint_default_gallery_id' not in fields:
            fields.append('tint_default_gallery_id')
        return fields

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if read_records and 'tint_default_gallery_id' not in read_records[0]:
            record = records.browse(read_records[0]['id'])
            read_records[0]['tint_default_gallery_id'] = record.tint_default_gallery_id.id or False
        return read_records


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_tint_default_gallery_id = fields.Many2one(
        related='pos_config_id.tint_default_gallery_id',
        readonly=False,
        string="Galería por defecto para colores TPV",
        help="Galería asignada automáticamente al registrar nuevos colores y fórmulas desde este Punto de Venta.",
    )
