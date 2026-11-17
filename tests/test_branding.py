"""The logo: the tab icon (favicon) on every page, and the mark in the sidebar and on the sign-in page."""
import re

import pytest

ICONS = {'favicon.ico': 'image/', 'favicon.svg': 'image/svg+xml', 'apple-touch-icon.png': 'image/png'}


def icon_links(html):
    """{file name: rel} for the icon links in a page's head."""
    return {href.split('?')[0].rsplit('/', 1)[-1]: rel
            for rel, href in re.findall(r'<link rel="(icon|apple-touch-icon)" href="([^"]+)"', html)}


def test_the_sign_in_page_links_every_icon_and_shows_the_mark(app):
    html = app.test_client().get('/login').get_data(as_text=True)
    assert icon_links(html) == {'favicon.ico': 'icon', 'favicon.svg': 'icon', 'apple-touch-icon.png': 'apple-touch-icon'}
    assert 'M15.8 18.6l2.4 2.4 3.3-4.6' in html  # the check in the logo's fourth corner


def test_signed_in_pages_link_the_icons_and_show_the_mark_in_the_sidebar(data, client_for):
    html = client_for(data.hod).get('/').get_data(as_text=True)
    assert set(icon_links(html)) == set(ICONS)
    sidebar = html.split('aria-label="AttendQR home"', 1)[1][:1500]
    assert 'M15.8 18.6l2.4 2.4 3.3-4.6' in sidebar


@pytest.mark.parametrize(('name', 'content_type'), ICONS.items())
def test_the_icon_files_are_served(app, name, content_type):
    response = app.test_client().get(f'/static/{name}')
    assert response.status_code == 200
    assert response.mimetype.startswith(content_type)
    assert len(response.data) > 200
