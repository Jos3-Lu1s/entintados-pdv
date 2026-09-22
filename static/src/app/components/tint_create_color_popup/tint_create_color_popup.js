import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { formatPoints } from "@entintados_pdv/app/utils/tint_points";

/**
 * Diálogo independiente para registrar un nuevo color (tint.color)
 * y opcionalmente su fórmula inicial (tint.color.formula).
 */
export class TintCreateColorPopup extends Component {
    static template = "entintados_pdv.TintCreateColorPopup";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        baseTypeId: { optional: true },
        sizeId: { optional: true },
        galleryId: { optional: true },
        getPayload: { type: Function, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: _t("Crear Color"),
        baseTypeId: false,
        sizeId: false,
        galleryId: false,
    };

    setup() {
        this.pos = useService("pos");
        this.notification = useService("notification");

        const galleryModel = this.pos.models["tint.gallery"];
        const allGalleries = galleryModel?.getAll() || [];

        // 1. Extraer ID configurado en pos.config
        let configGalleryId = null;
        const rawConfig = this.pos.config?.tint_default_gallery_id;
        if (rawConfig) {
            if (typeof rawConfig === "object") {
                configGalleryId = rawConfig.id || (Array.isArray(rawConfig) ? rawConfig[0] : null);
            } else if (typeof rawConfig === "number" || typeof rawConfig === "string") {
                configGalleryId = parseInt(rawConfig);
            }
        }

        // 2. Resolver galería: Prioridad 1: Configuración -> Prioridad 2: 'ODM' (Odoo Manual) -> Prioridad 3: Primera activa por secuencia
        let resolvedGallery = null;
        if (configGalleryId && galleryModel) {
            resolvedGallery = galleryModel.get(configGalleryId);
        }
        if (!resolvedGallery && allGalleries.length) {
            resolvedGallery = allGalleries.find((g) => (g.code || "").toUpperCase() === "ODM");
        }
        if (!resolvedGallery && allGalleries.length) {
            resolvedGallery = [...allGalleries].sort((a, b) => (a.sequence || 0) - (b.sequence || 0))[0];
        }

        const initialGalleryId = resolvedGallery ? String(resolvedGallery.id) : "";

        this.state = useState({
            // Datos del color
            newColorName: "",
            newColorCode: "",
            newColorNotes: "",

            // Galería por defecto, base y presentación para la fórmula
            newGalleryId: initialGalleryId,
            newBaseTypeId: this.props.baseTypeId ? String(this.props.baseTypeId) : "",
            newSizeId: this.props.sizeId ? String(this.props.sizeId) : "",

            // Líneas de fórmula y dosis
            selectedColorantId: "",
            newColorantPoints: 1,
            newFormulaLines: [],

            generateOtherSizes: true,

            isCreatingColor: false,
            createColorError: "",
            createColorSuccess: "",
        });
    }

    get defaultGallery() {
        const galleryId = parseInt(this.state.newGalleryId);
        if (galleryId && this.pos.models["tint.gallery"]) {
            return this.pos.models["tint.gallery"].get(galleryId) || null;
        }
        return null;
    }

    get galleries() {
        if (!this.pos.models["tint.gallery"]) {
            return [];
        }
        return [...this.pos.models["tint.gallery"].getAll()]
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0) || (a.name || "").localeCompare(b.name || ""));
    }

    get baseTypes() {
        if (!this.pos.models["tint.base.type"]) {
            return [];
        }
        return [...this.pos.models["tint.base.type"].getAll()]
            .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    get sizes() {
        if (!this.pos.models["tint.size"]) {
            return [];
        }
        return [...this.pos.models["tint.size"].getAll()]
            .sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    }

    get colorants() {
        if (!this.pos.models["product.product"]) {
            return [];
        }
        return [...this.pos.models["product.product"].getAll()]
            .filter((p) => p.tint_role === "colorant")
            .sort((a, b) => (a.display_name || a.name || "").localeCompare(b.display_name || b.name || ""));
    }

    get newCapacityPoints() {
        const baseTypeId = parseInt(this.state.newBaseTypeId);
        const sizeId = parseInt(this.state.newSizeId);
        if (!baseTypeId || !sizeId || !this.pos.models["tint.base.capacity"]) {
            return 0;
        }
        const cap = this.pos.models["tint.base.capacity"]
            .getAll()
            .find(
                (c) =>
                    c.base_type_id?.id === baseTypeId &&
                    c.size_id?.id === sizeId
            );
        return cap ? cap.max_points : 0;
    }

    get capacityPercent() {
        if (!this.newCapacityPoints || this.newCapacityPoints <= 0) {
            return 0;
        }
        return (this.newFormulaTotalPoints / this.newCapacityPoints) * 100;
    }

    get isOverCapacity() {
        return this.newCapacityPoints > 0 && this.newFormulaTotalPoints > this.newCapacityPoints;
    }

    get newFormulaTotalPoints() {
        return this.state.newFormulaLines.reduce((acc, l) => acc + (l.points || 0), 0);
    }

    get newFormulaCostMin() {
        return this.state.newFormulaLines.reduce((acc, l) => {
            const colorant = this.pos.models["product.product"].get(l.colorantId);
            const cost = colorant?.standard_price || 0;
            return acc + (l.points || 0) * cost;
        }, 0);
    }

    get newFormulaCostMax() {
        return this.state.newFormulaLines.reduce((acc, l) => {
            const colorant = this.pos.models["product.product"].get(l.colorantId);
            const price = colorant?.lst_price ?? colorant?.list_price ?? colorant?.product_tmpl_id?.list_price ?? 0;
            return acc + (l.points || 0) * price;
        }, 0);
    }

    formatCurrency(amount) {
        if (this.env.utils?.formatCurrency) {
            return this.env.utils.formatCurrency(amount);
        }
        return "$" + Number(amount || 0).toFixed(2);
    }

    formatPoints(total) {
        return formatPoints(total);
    }

    addColorantLine() {
        const colorantId = parseInt(this.state.selectedColorantId);
        const points = parseFloat(this.state.newColorantPoints);
        if (!colorantId || isNaN(colorantId)) {
            return;
        }
        if (!points || points <= 0 || isNaN(points)) {
            return;
        }
        const colorant = this.pos.models["product.product"].get(colorantId);
        if (!colorant) {
            return;
        }

        const existing = this.state.newFormulaLines.find((l) => l.colorantId === colorantId);
        if (existing) {
            existing.points = Number(points.toFixed(4));
            existing.highlighted = true;
            if (existing._highlightTimeout) {
                clearTimeout(existing._highlightTimeout);
            }
            existing._highlightTimeout = setTimeout(() => {
                existing.highlighted = false;
            }, 1000);
        } else {
            this.state.newFormulaLines.push({
                colorantId,
                colorantName: colorant.display_name || colorant.name,
                points: Number(points.toFixed(4)),
                highlighted: false,
            });
        }
        this.state.selectedColorantId = "";
        this.state.newColorantPoints = 1;
    }

    onUpdateColorantPoints(line, event) {
        const rawValue = event.target.value;
        if (rawValue === "" || rawValue === null || rawValue === undefined) {
            line.points = 0;
            return;
        }
        const val = parseFloat(rawValue);
        line.points = !isNaN(val) && val >= 0 ? val : 0;
    }

    removeColorantLine(index) {
        const line = this.state.newFormulaLines[index];
        if (line?._highlightTimeout) {
            clearTimeout(line._highlightTimeout);
        }
        this.state.newFormulaLines.splice(index, 1);
    }

    get canSaveColor() {
        if (this.state.isCreatingColor) {
            return false;
        }
        if (!this.state.newColorName.trim() || !this.state.newColorCode.trim()) {
            return false;
        }
        if (!parseInt(this.state.newGalleryId) || !parseInt(this.state.newBaseTypeId) || !parseInt(this.state.newSizeId)) {
            return false;
        }
        if (!this.state.newFormulaLines.length || this.newFormulaTotalPoints <= 0) {
            return false;
        }
        if (this.state.newFormulaLines.some((l) => !l.points || l.points <= 0 || isNaN(l.points))) {
            return false;
        }
        if (this.isOverCapacity) {
            return false;
        }
        return true;
    }

    async saveNewColor() {
        if (!this.state.newColorName.trim()) {
            this.state.createColorError = _t("El nombre del color es obligatorio.");
            return;
        }
        if (!this.state.newColorCode.trim()) {
            this.state.createColorError = _t("El código del color es obligatorio.");
            return;
        }

        const galleryId = parseInt(this.state.newGalleryId);
        if (!galleryId) {
            this.state.createColorError = _t("No se encontró una galería por defecto válida para el TPV.");
            return;
        }

        const baseTypeId = parseInt(this.state.newBaseTypeId);
        if (!baseTypeId) {
            this.state.createColorError = _t("Debes seleccionar un Tipo de Base.");
            return;
        }

        const sizeId = parseInt(this.state.newSizeId);
        if (!sizeId) {
            this.state.createColorError = _t("Debes seleccionar una Presentación.");
            return;
        }

        if (!this.state.newFormulaLines.length) {
            this.state.createColorError = _t("Debes agregar al menos un colorante a la fórmula.");
            return;
        }

        const invalidLine = this.state.newFormulaLines.find(
            (l) => !l.points || l.points <= 0 || isNaN(l.points)
        );
        if (invalidLine) {
            this.state.createColorError = _t(
                "Todos los colorantes deben tener una dosis válida mayor a cero (colorante «%s»).",
                invalidLine.colorantName || invalidLine.colorantId
            );
            return;
        }

        if (this.newFormulaTotalPoints <= 0) {
            this.state.createColorError = _t("La dosificación total de la fórmula debe ser mayor a cero.");
            return;
        }

        if (this.newCapacityPoints > 0 && this.newFormulaTotalPoints > this.newCapacityPoints) {
            this.state.createColorError = _t(
                "La dosis total de la fórmula (%s) excede la capacidad del envase (%s).",
                this.formatPoints(this.newFormulaTotalPoints),
                this.formatPoints(this.newCapacityPoints)
            );
            return;
        }

        this.state.isCreatingColor = true;
        this.state.createColorError = "";
        this.state.createColorSuccess = "";

        try {
            // 1. Crear tint.color
            const vals = {
                name: this.state.newColorName.trim(),
                code: this.state.newColorCode.trim().toUpperCase(),
            };
            if (this.state.newColorNotes.trim()) {
                vals.notes = this.state.newColorNotes.trim();
            }

            let colorRecord = null;
            if (this.pos.data && typeof this.pos.data.create === "function") {
                const res = await this.pos.data.create("tint.color", [vals]);
                colorRecord = Array.isArray(res) ? res[0] : res;
            } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                const ids = await this.pos.orm.create("tint.color", [vals]);
                const createdId = Array.isArray(ids) ? ids[0] : ids;
                colorRecord = this.pos.models["tint.color"]?.get(createdId);
            }

            const colorId = colorRecord ? colorRecord.id || colorRecord : false;
            if (!colorId) {
                throw new Error(_t("No se pudo obtener el identificador del color creado."));
            }

            // 2. Crear fórmula y líneas de dosis
            const formulaVals = {
                color_id: colorId,
                gallery_id: galleryId,
                base_type_id: baseTypeId,
                size_id: sizeId,
            };
            let formulaRecord = null;
            if (this.pos.data && typeof this.pos.data.create === "function") {
                const fRes = await this.pos.data.create("tint.color.formula", [formulaVals]);
                formulaRecord = Array.isArray(fRes) ? fRes[0] : fRes;
            } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                const fIds = await this.pos.orm.create("tint.color.formula", [formulaVals]);
                const fId = Array.isArray(fIds) ? fIds[0] : fIds;
                formulaRecord = this.pos.models["tint.color.formula"]?.get(fId);
            }

            const formulaId = formulaRecord ? formulaRecord.id || formulaRecord : false;
            if (!formulaId) {
                throw new Error(_t("No se pudo obtener el identificador de la fórmula creada."));
            }

            const lineValsList = this.state.newFormulaLines.map((l, idx) => ({
                formula_id: formulaId,
                colorant_id: l.colorantId,
                points: Number(parseFloat(l.points).toFixed(4)),
                sequence: (idx + 1) * 10,
            }));
            if (this.pos.data && typeof this.pos.data.create === "function") {
                await this.pos.data.create("tint.color.formula.line", lineValsList);
            } else if (this.pos.orm && typeof this.pos.orm.create === "function") {
                await this.pos.orm.create("tint.color.formula.line", lineValsList);
            }

            if (this.state.generateOtherSizes) {
                try {
                    let genRes = null;
                    if (this.pos.orm && typeof this.pos.orm.call === "function") {
                        genRes = await this.pos.orm.call(
                            "tint.color.formula",
                            "generate_other_sizes_pos",
                            [[formulaId], this.pos.config.id]
                        );
                    } else if (this.pos.data && typeof this.pos.data.call === "function") {
                        genRes = await this.pos.data.call(
                            "tint.color.formula",
                            "generate_other_sizes_pos",
                            [[formulaId], this.pos.config.id]
                        );
                    }

                    if (genRes?.data && this.pos.data?.models?.connectNewData) {
                        this.pos.data.models.connectNewData(genRes.data);
                    } else {
                        if (genRes?.["tint.color.formula"]?.length && this.pos.models["tint.color.formula"]?.load) {
                            this.pos.models["tint.color.formula"].load(genRes["tint.color.formula"]);
                        }
                        if (genRes?.["tint.color.formula.line"]?.length && this.pos.models["tint.color.formula.line"]?.load) {
                            this.pos.models["tint.color.formula.line"].load(genRes["tint.color.formula.line"]);
                        }
                    }

                    const createdCount = genRes?.created_count || 0;
                    const createdNames = (genRes?.created_sizes || []).join(", ");
                    const omittedSizes = genRes?.omitted_sizes || [];

                    if (createdCount > 0 && omittedSizes.length > 0) {
                        this.notification.add(
                            _t(
                                "Color registrado. Se generaron las presentaciones (%s), pero se omitieron por exceder la capacidad del envase: %s.",
                                createdNames || createdCount,
                                omittedSizes.join(", ")
                            ),
                            { type: "warning" }
                        );
                    } else if (createdCount > 0) {
                        const msg = createdNames
                            ? _t("¡Color registrado y presentaciones generadas: %s!", createdNames)
                            : _t("¡Color y %s presentaciones adicionales generados exitosamente!", createdCount);
                        this.notification.add(msg, { type: "success" });
                    } else if (omittedSizes.length > 0) {
                        this.notification.add(
                            _t(
                                "Color registrado, pero las demás presentaciones no se generaron porque exceden la capacidad máxima del envase: %s.",
                                omittedSizes.join(", ")
                            ),
                            { type: "warning" }
                        );
                    } else {
                        this.notification.add(
                            _t("¡Color y fórmula registrados exitosamente!"),
                            { type: "success" }
                        );
                    }
                } catch (genError) {
                    console.warn("Error al generar presentaciones automáticas:", genError);
                    const errorMsg = genError?.data?.message || genError?.message;
                    this.notification.add(
                        errorMsg || _t("Fórmula inicial guardada. Puedes generar las demás presentaciones desde el panel de entintado."),
                        { type: "warning" }
                    );
                }
            } else {
                this.notification.add(
                    _t("¡Color y fórmula registrados exitosamente!"),
                    { type: "success" }
                );
            }

            // Sincronizar el registro del color con el backend para actualizar base_type_summary y demás campos
            try {
                const colorFields = [
                    "id",
                    "name",
                    "display_name",
                    "code",
                    "has_formula",
                    "base_type_summary",
                ];
                let colorRead = null;
                if (this.pos.data && typeof this.pos.data.read === "function") {
                    const res = await this.pos.data.read("tint.color", [colorId], colorFields);
                    colorRead = Array.isArray(res) ? res[0] : res;
                } else if (this.pos.orm && typeof this.pos.orm.read === "function") {
                    const res = await this.pos.orm.read("tint.color", [colorId], colorFields);
                    colorRead = Array.isArray(res) ? res[0] : res;
                }

                if (colorRead) {
                    if (this.pos.data?.models?.connectNewData) {
                        this.pos.data.models.connectNewData({ "tint.color": [colorRead] });
                    }
                    const localColor = this.pos.models["tint.color"]?.get(colorId);
                    if (localColor) {
                        if (typeof localColor.update === "function") {
                            localColor.update(colorRead, { omitUnknownField: true });
                        }
                        Object.assign(localColor, colorRead);
                    }
                    colorRecord = localColor || colorRead;
                } else {
                    const baseType = this.pos.models["tint.base.type"]?.get(baseTypeId);
                    if (baseType?.name) {
                        const localColor = this.pos.models["tint.color"]?.get(colorId);
                        if (localColor) {
                            localColor.base_type_summary = baseType.name;
                            localColor.has_formula = true;
                        }
                    }
                }
            } catch (readErr) {
                console.warn("No se pudo refrescar tint.color tras crear la fórmula:", readErr);
                const baseType = this.pos.models["tint.base.type"]?.get(baseTypeId);
                if (baseType?.name) {
                    const localColor = this.pos.models["tint.color"]?.get(colorId);
                    if (localColor) {
                        localColor.base_type_summary = baseType.name;
                        localColor.has_formula = true;
                    }
                }
            }

            this.props.getPayload?.({
                colorId,
                color: colorRecord,
                formulaId,
                galleryId,
                baseTypeId,
                sizeId,
            });

            this.props.close();
        } catch (error) {
            console.error("Error al crear el color/fórmula:", error);
            this.state.createColorError =
                error?.data?.message ||
                error?.message ||
                _t("No se pudo crear el color/fórmula. Verifica los datos introducidos.");
        } finally {
            this.state.isCreatingColor = false;
        }
    }

    cancel() {
        this.props.close();
    }
}
