import re
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import urlsplit

from lxml import html as lxml_html


def _inline_assets(env, source):
    """Embed stylesheets and drop remote resources so wkhtmltopdf never calls back the server."""
    if not source:
        return source
    root = lxml_html.fromstring(source)
    for link in root.xpath('//link[@href]'):
        asset = env['ir.attachment'].search([('url', '=', urlsplit(link.get('href')).path)], limit=1)
        if asset and asset.raw:
            style = lxml_html.Element('style')
            style.text = re.sub(r'url\([^)]*\)', 'none', asset.raw.decode('utf-8'))
            link.addprevious(style)
        link.drop_tree()
    for node in root.xpath('//script[@src] | //base'):
        node.drop_tree()
    for image in root.xpath('//img[@src]'):
        if not image.get('src').startswith('data:'):
            image.drop_tree()
    return lxml_html.tostring(root, encoding='unicode')


@contextmanager
def offline_wkhtmltopdf(env):
    """Run wkhtmltopdf inside a test transaction without calling back the HTTP server.

    wkhtmltopdf downloads the report assets from the HTTP server, whose cursor waits for the
    records the test has not committed while the test waits for wkhtmltopdf: a deadlock with
    no timeout. Inlining the assets keeps the rendering local to the test. It covers both
    report actions and direct exports such as accounting reports.
    """
    report_model = env['ir.actions.report']
    native = report_model._run_wkhtmltopdf

    def offline(bodies, *args, **kwargs):
        for key in ('header', 'footer'):
            if key in kwargs:
                kwargs[key] = _inline_assets(env, kwargs[key])
        return native([_inline_assets(env, body) for body in bodies], *args, **kwargs)

    with patch.object(type(report_model), '_run_wkhtmltopdf', side_effect=offline):
        yield


def render_offline_pdf(renderer, report_ref, ids, data=None):
    """Render a real PDF of a report action inside a test transaction."""
    renderer = renderer.with_context(force_report_rendering=True)
    with offline_wkhtmltopdf(renderer.env):
        return renderer._render_qweb_pdf(report_ref, ids, data=data)
