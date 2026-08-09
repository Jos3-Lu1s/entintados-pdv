/** @odoo-module **/

/**
 * OBSOLETO — este archivo ya no se carga.
 *
 * Extendía `ControlButtons` con un segundo botón de pincel que duplicaba al
 * de `js/tint_control_button.js`. Dos botones con el mismo icono hacían
 * imposible distinguir cuál se estaba pulsando, y su método
 * `openTintColorScreen` buscaba una categoría POS «Carta de Colores» que ya
 * no existe desde que se eliminaron los productos virtuales.
 *
 * Todo vive ahora en:
 *   - `static/src/js/tint_control_button.js`
 *   - `static/src/xml/tint_control_button.xml`
 *
 * Se deja el archivo vacío en lugar de borrarlo para no romper instalaciones
 * que aún tengan los assets en caché. Puede eliminarse en la siguiente
 * limpieza.
 */
