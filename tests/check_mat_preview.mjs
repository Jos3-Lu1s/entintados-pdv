import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class AttachmentView {}
class PopoutAttachmentView {}
const source = fs.readFileSync(new URL('../static/src/views/mat_invoice_preview.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?$/gm, '');
vm.runInNewContext(source, { AttachmentView, PopoutAttachmentView,
    patch(target, extension) { Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension)); } });
for (const View of [AttachmentView, PopoutAttachmentView]) {
    const view = new View();
    view.matPreview = { url: '', attachmentId: false };
    view.state = { thread: { message_main_attachment_id: { id: 12, defaultSource: 'original.pdf' } } };
    let calls = 0;
    view.matOrm = { async call() { calls++; return { url: '/report/pdf/current/3', attachment_id: 12 }; } };
    await view.loadMatPreview({ threadModel: 'sale.order', threadId: 3 });
    assert.equal(calls, 0);
    assert.equal(view.matPdfSource, 'original.pdf');
    await view.loadMatPreview({ threadModel: 'account.move', threadId: 3 });
    assert.ok(decodeURIComponent(view.matPdfSource).includes('/report/pdf/current/3'));
    view.state.thread.message_main_attachment_id = { id: 99, defaultSource: 'supplement.pdf' };
    assert.equal(view.matPdfSource, 'supplement.pdf');
    view.matOrm = { async call() { throw new Error('offline'); } };
    await view.loadMatPreview({ threadModel: 'account.move', threadId: 3 });
    assert.equal(view.matPdfSource, 'supplement.pdf');
    const pending = [];
    view.matOrm = { call() { return new Promise(resolve => pending.push(resolve)); } };
    const first = view.loadMatPreview({ threadModel: 'account.move', threadId: 3 });
    const second = view.loadMatPreview({ threadModel: 'account.move', threadId: 4 });
    pending[1]({ url: '/new', attachment_id: 99 }); await second;
    pending[0]({ url: '/old', attachment_id: 12 }); await first;
    assert.ok(decodeURIComponent(view.matPdfSource).includes('/new'));
}
console.log('PASS: current PDF, extra attachments, RPC fallback and navigation races');
