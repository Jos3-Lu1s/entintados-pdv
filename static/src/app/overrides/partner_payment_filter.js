import { PartnerList } from '@point_of_sale/app/screens/partner_list/partner_list';
import { ResPartner } from '@point_of_sale/app/models/res_partner';
import { patch } from '@web/core/utils/patch';

patch(ResPartner.prototype, {
    get searchString() {
        return `${super.searchString} ${this.company_registry || ''}`.trim();
    },
});

patch(PartnerList.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.customerPaymentFilter = '';
        this.customerPaymentOffsets = new Map();
    },

    async setCustomerPaymentFilter(filter) {
        this.state.customerPaymentFilter = this.state.customerPaymentFilter === filter ? '' : filter;
        await this.getNewPartners();
    },

    getPartners(partners) {
        const filter = this.state.customerPaymentFilter;
        const candidates = filter ? partners.filter((partner) => {
            const credit = Boolean(partner.pos_contact_info?.credit_authorized ??
                partner.commercial_partner_id?.is_credit ?? partner.is_credit);
            return credit === (filter === 'credit');
        }) : partners;
        return super.getPartners(candidates);
    },

    _getSearchFields(query) {
        return [...new Set([...super._getSearchFields(query), 'company_registry'])];
    },

    async getNewPartners() {
        const filter = this.state.customerPaymentFilter;
        if (!filter) {
            return super.getNewPartners(...arguments);
        }
        if (this.state.loading) {
            return [];
        }
        const query = this.state.query || '';
        const key = JSON.stringify([filter, query]);
        const offset = this.customerPaymentOffsets.get(key) || 0;
        if (offset === -1) {
            return [];
        }
        const domain = [['commercial_partner_id.is_credit', '=', filter === 'credit']];
        if (query) {
            const fields = this._getSearchFields(query);
            domain.push(...Array(fields.length - 1).fill('|'),
                ...fields.map((field) => [field, 'ilike', query]));
        }
        try {
            this.state.loading = true;
            const result = await this.pos.data.callRelated('res.partner', 'get_new_partner', [
                this.pos.config.id, domain, offset,
            ]);
            const partners = result['res.partner'];
            this.customerPaymentOffsets.set(key, partners.length ? offset + partners.length : -1);
            for (const partner of partners) {
                if (!this.loadedPartnerIds.has(partner.id)) {
                    this.loadedPartnerIds.add(partner.id);
                    this.state.loadedPartners.push(partner);
                }
            }
            return partners;
        } catch {
            return [];
        } finally {
            this.state.loading = false;
        }
    },
});
