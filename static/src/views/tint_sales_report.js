import { Component, useSubEnv } from "@odoo/owl";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

class TintDocumentLink extends Component {
    static template = "entintados_pdv.TintDocumentLink";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    async openDocument(event) {
        event.stopPropagation();
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
            return;
        }
        event.preventDefault();
        const action = await this.orm.call("tint.sales.report", "action_open_document", [
            [this.props.record.resId],
        ]);
        await this.action.doAction(action);
    }
}

registry.category("fields").add("tint_document_link", {
    component: TintDocumentLink,
    supportedTypes: ["reference"],
    fieldDependencies: [
        { name: "reference", type: "char" },
        { name: "document_url", type: "char" },
    ],
});

class TintSalesListController extends ListController {
    setup() {
        super.setup();
        useSubEnv({ tintShowMargin: () => Boolean(this.optionalActiveFields.margin) });
    }

    get actionMenuProps() {
        const props = super.actionMenuProps;
        const optionalFields = this.optionalActiveFields;
        return {
            ...props,
            context: {
                ...props.context,
                get tint_show_margin() {
                    return Boolean(optionalFields.margin);
                },
            },
        };
    }
}

registry.category("views").add("tint_sales_report_list", {
    ...listView,
    Controller: TintSalesListController,
});

class TintSalesReportExport extends Component {
    static template = "entintados_pdv.TintSalesReportExport";
    static components = { DropdownItem };
    static props = {};

    get exportData() {
        return {
            domain: JSON.stringify(this.env.searchModel.domain),
            companies: JSON.stringify(this.env.model.root.context.allowed_company_ids || []),
            show_margin: this.env.tintShowMargin?.() ? "1" : "0",
        };
    }

    downloadExcel() {
        return download({ url: "/entintados_pdv/tint-sales/xlsx", data: this.exportData });
    }

    openPdf() {
        const form = document.createElement("form");
        form.method = "POST";
        form.action = "/entintados_pdv/tint-sales/pdf";
        form.target = "_blank";
        form.rel = "noopener";
        for (const [name, value] of Object.entries({ ...this.exportData, csrf_token: odoo.csrf_token })) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = name;
            input.value = value;
            form.append(input);
        }
        document.body.append(form);
        try {
            form.submit();
        } finally {
            form.remove();
        }
    }
}

registry.category("cogMenu").add("entintados-sales-export", {
    Component: TintSalesReportExport,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,
    isDisplayed: (env) => env.config.viewType === "list" &&
        env.model.root.resModel === "tint.sales.report" && !env.model.root.selection.length,
}, { sequence: 20 });
