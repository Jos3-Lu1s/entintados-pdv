/** @odoo-module **/
import { onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { AttachmentView, PopoutAttachmentView } from "@mail/core/common/attachment_view";

function extension() {
    return {
        setup() {
            super.setup(...arguments);
            this.matOrm = useService("orm");
            this.matPreview = useState({ url: "", attachmentId: false });
            onWillStart(() => this.loadMatPreview(this.props));
            onWillUpdateProps((props) => this.loadMatPreview(props));
        },
        async loadMatPreview(props) {
            const request = (this.matPreviewRequest || 0) + 1;
            this.matPreviewRequest = request;
            this.matPreview.url = "";
            this.matPreview.attachmentId = false;
            if (props.threadModel !== "account.move" || !props.threadId) return;
            let result;
            try {
                result = await this.matOrm.call("account.move", "action_get_mat_preview", [[props.threadId]]);
            } catch {
                return; // Keep the native attachment viewer available on RPC failures.
            }
            if (request !== this.matPreviewRequest) return;
            if (result) {
                this.matPreview.url = "/web/static/lib/pdfjs/web/viewer.html?file=" +
                    encodeURIComponent(result.url + "?mat_preview=" + Date.now());
                this.matPreview.attachmentId = result.attachment_id;
            }
        },
        get matPdfSource() {
            const attachment = this.state.thread.message_main_attachment_id;
            return this.matPreview.url && attachment?.id === this.matPreview.attachmentId
                ? this.matPreview.url : attachment?.defaultSource;
        },
    };
}
patch(AttachmentView.prototype, extension());
patch(PopoutAttachmentView.prototype, extension());
