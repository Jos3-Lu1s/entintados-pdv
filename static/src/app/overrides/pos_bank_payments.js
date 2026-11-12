import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

function cardKind(method) {
    if (method.card_payment_code === "04") {
        return "credit";
    }
    if (method.card_payment_code === "28" || method.collection_type === "card") {
        return "debit";
    }
    return false;
}

function hasFixedBank(method) {
    return Boolean(method.card_bank_name || method.terminal_label);
}

patch(PaymentScreen.prototype, {
    get terminalBankPaymentMethods() {
        const methods = this.payment_methods_from_config;
        const genericKinds = new Set(methods.filter((method) => !hasFixedBank(method)).map(cardKind));
        return methods.filter((method) => !cardKind(method) || !hasFixedBank(method) ||
            !genericKinds.has(cardKind(method)));
    },

    async addNewPaymentLine(paymentMethod) {
        if (!cardKind(paymentMethod) || hasFixedBank(paymentMethod)) {
            return super.addNewPaymentLine(...arguments);
        }
        if (this.selectingTerminalBank) {
            return false;
        }
        const order = this.currentOrder;
        const banks = this.pos.models["pos.card.bank"].getAll().filter((bank) => bank.active);
        if (!banks.length) {
            this.dialog.add(AlertDialog, {
                title: _t("Banco de la terminal"),
                body: _t("Agrega un banco en Punto de Venta → Configuración → Bancos de tarjeta y recarga la caja."),
            });
            return false;
        }
        this.selectingTerminalBank = true;
        try {
            const bank = await makeAwaitable(this.dialog, SelectionPopup, {
                title: _t("Selecciona el banco de la terminal"),
                list: banks.map((bank) => ({ id: bank.id, label: bank.name, item: bank })),
            });
            if (!bank || this.currentOrder !== order) {
                return false;
            }
            const existing = order.payment_ids.find((line) =>
                line.payment_method_id === paymentMethod && !line.terminal_bank_id);
            if (existing) {
                existing.update({ terminal_bank_id: bank });
                order.selectPaymentline(existing);
                return true;
            }
            const previousLines = new Set(order.payment_ids);
            const added = await super.addNewPaymentLine(...arguments);
            if (added) {
                const line = order.payment_ids.find((payment) =>
                    payment.payment_method_id === paymentMethod && !previousLines.has(payment));
                line?.update({ terminal_bank_id: bank });
            }
            return added;
        } finally {
            this.selectingTerminalBank = false;
        }
    },
});

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(...args) {
        const missing = this.order.payment_ids.some((line) =>
            line.amount && cardKind(line.payment_method_id) &&
            !hasFixedBank(line.payment_method_id) && !line.terminal_bank_id);
        if (missing) {
            this.pos.dialog.add(AlertDialog, {
                title: _t("Falta el banco de la terminal"),
                body: _t("Pulsa el método de tarjeta del pago para seleccionar el banco."),
            });
            return false;
        }
        return super.isOrderValid(...args);
    },
});
