import { Component, useEffect, useState } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

export class CustomerCreditBadge extends Component {
    static template = "entintados_pdv.CustomerCreditBadge";
    static props = { partner: Object, pos: Object, orderUuid: { type: String, optional: true } };

    setup() {
        this.state = useState({ partnerId: false, loaded: false, authorized: false, available: 0 });
        this._request = 0;
        useEffect(() => {
            this.refreshCredit();
            return () => { this._request++; };
        }, () => [this.props.partner.id, this.props.pos.config.id, this.props.orderUuid,
            this.props.partner.total_due, this.props.partner.credit_limit, this.props.partner.is_credit]);
    }

    get visible() {
        return this.state.loaded && this.state.partnerId === this.props.partner.id &&
            this.state.authorized && this.state.available > 0;
    }

    async refreshCredit() {
        const partnerId = this.props.partner.id;
        const configId = this.props.pos.config.id;
        const request = ++this._request;
        Object.assign(this.state, { partnerId, loaded: false, authorized: false, available: 0 });
        try {
            const info = await this.props.pos.data.call("res.partner", "get_advance_pos_info", [[partnerId], configId]);
            if (request !== this._request || this.props.partner.id !== partnerId ||
                this.props.pos.config.id !== configId) return;
            const available = Number(info.purchase_power);
            Object.assign(this.state, {
                loaded: true, authorized: Boolean(info.credit_authorized),
                available: Number.isFinite(available) ? Math.max(available, 0) : 0,
            });
        } catch {
            // An unavailable balance must never leave the previous customer's badge visible.
        }
    }
}

ProductScreen.components = { ...ProductScreen.components, CustomerCreditBadge };
PaymentScreen.components = { ...PaymentScreen.components, CustomerCreditBadge };
