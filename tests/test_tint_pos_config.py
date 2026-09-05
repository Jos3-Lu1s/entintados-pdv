# -*- coding: utf-8 -*-

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTintPosConfig(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.galleries = cls.env['tint.gallery']
        cls.pos_configs = cls.env['pos.config']

        cls.gallery_default = cls.galleries.create({
            'name': 'Galería TPV Default',
            'code': 'TPVDEF',
            'sequence': 5,
        })
        cls.gallery_secondary = cls.galleries.create({
            'name': 'Galería TPV Secundaria',
            'code': 'TPVSEC',
            'sequence': 15,
        })

        cls.pos_config = cls.pos_configs.create({
            'name': 'Punto de Venta Entintado Test',
            'tint_default_gallery_id': cls.gallery_default.id,
        })

    def test_pos_config_default_gallery_assignment(self):
        """Verifica que pos.config almacene y retorne correctamente la galería por defecto."""
        self.assertEqual(
            self.pos_config.tint_default_gallery_id,
            self.gallery_default,
            "La galería asignada debe coincidir con la configurada.",
        )

        # Modificación de galería
        self.pos_config.write({'tint_default_gallery_id': self.gallery_secondary.id})
        self.assertEqual(
            self.pos_config.tint_default_gallery_id,
            self.gallery_secondary,
            "La galería debe actualizarse al cambiar el valor.",
        )

    def test_pos_config_load_pos_data_fields(self):
        """Verifica que _load_pos_data_fields y _load_pos_data_read incluyan tint_default_gallery_id."""
        fields_list = self.pos_config._load_pos_data_fields(self.pos_config)
        # Si devuelve campos explícitos, tint_default_gallery_id debe estar en la lista
        if fields_list:
            self.assertIn(
                'tint_default_gallery_id',
                fields_list,
                "tint_default_gallery_id debe estar incluido en _load_pos_data_fields.",
            )

        # Verificar _load_pos_data_read
        read_data = self.pos_config._load_pos_data_read(self.pos_config, self.pos_config)
        self.assertTrue(read_data, "Debe retornar los datos del pos.config.")
        self.assertIn(
            'tint_default_gallery_id',
            read_data[0],
            "El payload de lectura para el TPV debe contener tint_default_gallery_id.",
        )
        self.assertEqual(
            read_data[0]['tint_default_gallery_id'],
            self.gallery_default.id,
            "El ID de la galería serializado debe coincidir con el asignado.",
        )

    def test_res_config_settings_related_field(self):
        """Verifica que res.config.settings interactúe con el pos.config mediante el related field."""
        settings = self.env['res.config.settings'].create({
            'pos_config_id': self.pos_config.id,
            'pos_tint_default_gallery_id': self.gallery_secondary.id,
        })
        self.assertEqual(
            self.pos_config.tint_default_gallery_id,
            self.gallery_secondary,
            "Modificar pos_tint_default_gallery_id en settings debe actualizar pos.config.",
        )

    def test_pos_config_without_default_gallery(self):
        """Verifica que un pos.config sin galería asignada devuelva False limpiamente."""
        pos_empty = self.pos_configs.create({
            'name': 'Punto de Venta Sin Galeria',
        })
        self.assertFalse(pos_empty.tint_default_gallery_id)

        read_data = pos_empty._load_pos_data_read(pos_empty, pos_empty)
        self.assertIn('tint_default_gallery_id', read_data[0])
        self.assertFalse(read_data[0]['tint_default_gallery_id'])

    def test_load_pos_data_models_standard_pos(self):
        """Verifica que una sesión de TPV estándar incluya los modelos de entintado."""
        models = self.env['pos.session']._load_pos_data_models(self.pos_config)
        self.assertIn('tint.gallery', models)
        self.assertIn('tint.color', models)
        self.assertIn('tint.color.formula', models)
        self.assertIn('tint.base.type', models)

    def test_load_pos_data_models_restaurant_pos(self):
        """Verifica que una sesión de TPV Bar/Restaurante NO incluya modelos de entintado."""
        pos_restaurant = self.pos_configs.create({
            'name': 'Bar Restaurante Test',
            'module_pos_restaurant': True,
        })
        models = self.env['pos.session']._load_pos_data_models(pos_restaurant)
        self.assertNotIn('tint.gallery', models)
        self.assertNotIn('tint.color', models)
        self.assertNotIn('tint.color.formula', models)
        self.assertNotIn('tint.schema', models)

    def test_load_pos_data_fields_restaurant_pos(self):
        """Verifica que un TPV Bar/Restaurante no incluya tint_default_gallery_id en fields ni read."""
        pos_restaurant = self.pos_configs.create({
            'name': 'Bar Restaurante Fields Test',
            'module_pos_restaurant': True,
        })
        fields_list = pos_restaurant._load_pos_data_fields(pos_restaurant)
        if fields_list:
            self.assertNotIn('tint_default_gallery_id', fields_list)

        read_data = pos_restaurant._load_pos_data_read(pos_restaurant, pos_restaurant)
        if read_data:
            self.assertNotIn('tint_default_gallery_id', read_data[0])
