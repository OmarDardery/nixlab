#!/usr/bin/env python3
"""Render repository Markdown into a small, searchable GitHub Pages website."""
import argparse
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

import markdown

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'website'
DOMAIN = 'https://nixlab.dardery.work'
REPO = 'https://github.com/OmarDardery/nixlab'
GROUPS = [
    ('GET STARTED', [('index', 'Overview'), ('plain-nixos', 'Install Nixlab'), ('start', 'First steps'), ('architecture', 'Architecture'), ('homelab', 'Meet the stack')]),
    ('USE YOUR SERVER', [('administration', 'Accounts & access'), ('networking', 'Networking'), ('guest-deployments', 'Guest updates'), ('file-modules', 'File configuration'), ('hardware', 'Hardware & disks'), ('helpers', 'Application helpers'), ('setup', 'Optional web wizard')]),
    ('WHEN THINGS HAPPEN', [('troubleshooting', 'Troubleshooting'), ('recovery', 'Backups & recovery'), ('operations', 'Operations & updates'), ('commissioning', 'Commissioning'), ('trial', 'Trial checklist'), ('validation', 'What is tested')]),
    ('PROJECT NOTES', [('deployment', 'Manual deployment'), ('configuration-contract', 'Configuration contract'), ('ownership', 'Ownership'), ('migration', 'Migration'), ('qualification', 'Release qualification'), ('roadmap', 'Roadmap'), ('website', 'Website & DNS')]),
]
DESCRIPTIONS = {
    'index': 'Find the install guide, understand the stack, or skip directly to whatever is currently broken.',
    'plain-nixos': 'One command on an installed NixOS server. Preserve the machine, configure the stack and verify access.',
    'troubleshooting': 'Symptoms, checks and recovery commands from actual installation failures. Fewer guesses, better logs.',
    'recovery': 'NixOS can roll back code. Your databases would like a separate plan.',
    'commissioning': 'Prove the stack works on your hardware, including the very important second boot.',
    'website': 'Publish the docs with GitHub Pages and connect nixlab.dardery.work.',
}


def url(base, slug='index'):
    return f'{base}/docs/' + (slug + '/' if slug != 'index' else '')


def navigation(base, current, pages):
    groups = []
    for title, items in GROUPS:
        links = ''.join(f'<a href="{url(base, slug)}"' + (' aria-current="page"' if current == slug else '') + f'>{escape(label)}</a>' for slug, label in items if slug in pages)
        groups.append(f'<section><h2>{title}</h2>{links}</section>')
    return ''.join(groups)


def rewrite_link(target, source, base):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith(('#', '/')):
        return target
    path = (source.parent / unquote(parsed.path)).resolve()
    suffix = ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
    if path.parent == ROOT / 'docs' and path.suffix == '.md':
        return url(base, path.stem) + suffix
    if path == ROOT / 'README.md':
        # README anchors belong to the actual README, not the landing page.
        return REPO + '/blob/main/README.md' + suffix
    if path.is_relative_to(ROOT) and path.exists():
        kind = 'tree' if path.is_dir() else 'blob'
        return REPO + '/' + kind + '/main/' + str(path.relative_to(ROOT)) + suffix
    return target


def render(source, base):
    text = source.read_text()
    title = text.splitlines()[0].lstrip('# ').strip()
    body = '\n'.join(text.splitlines()[1:]).lstrip()
    md = markdown.Markdown(extensions=['fenced_code', 'tables', 'toc', 'sane_lists'], extension_configs={'toc': {'toc_depth': '2-3'}}, output_format='xhtml')
    root = ET.fromstring('<div>' + md.convert(body) + '</div>')
    for node in root.iter():
        if 'href' in node.attrib:
            node.set('href', rewrite_link(node.get('href'), source, base))
        if 'src' in node.attrib:
            node.set('src', rewrite_link(node.get('src'), source, base))
    counter = 0
    for parent in list(root.iter()):
        for node in list(parent):
            if node.tag == 'table':
                wrapper = ET.Element('div', {'class': 'table-scroll', 'tabindex': '0', 'role': 'region', 'aria-label': 'Scrollable table'})
                parent.insert(list(parent).index(node), wrapper); parent.remove(node); wrapper.append(node)
            elif node.tag == 'pre' and node.find('code') is not None:
                counter += 1
                identifier = 'nixlab-code-' + str(counter)
                node.find('code').set('id', identifier)
                wrapper = ET.Element('div', {'class': 'code-wrap'})
                parent.insert(list(parent).index(node), wrapper); parent.remove(node)
                button = ET.SubElement(wrapper, 'button', {'type': 'button', 'class': 'copy-button', 'data-copy': identifier, 'aria-label': 'Copy code block ' + str(counter)})
                button.text = 'Copy'
                wrapper.append(node)
    serial = ''.join(ET.tostring(node, encoding='unicode', method='html') for node in root)
    return title, serial, md.toc, ' '.join(root.itertext())


def template(values):
    text = (SOURCE / 'templates/doc.html').read_text()
    # Replace in one pass so document content cannot become a template directive.
    return re.sub(r'@@([A-Z]+)@@', lambda match: values[match[1]], text)


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = set(); self.links = []; self.text = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get('id'): self.ids.add(values['id'])
        if tag in ('a', 'link') and values.get('href'): self.links.append(values['href'])
        if tag in ('img', 'script') and values.get('src'): self.links.append(values['src'])
    def handle_data(self, data):
        self.text.append(data)


def check(output, base):
    pages = {}
    for path in output.rglob('*.html'):
        parser = PageLinks(); text = path.read_text(); parser.feed(text); pages[path] = parser
        if re.search(r'@@[A-Z]+@@', text): raise ValueError('Unresolved template: ' + str(path))
    failures = []
    for sheet in (output / 'assets').glob('*.css'):
        for asset in re.findall(r'url\([\'"]?([^\)\'\"]+)', sheet.read_text()):
            if urlsplit(asset).scheme or asset.startswith('data:'): continue
            if not (sheet.parent / asset).is_file(): failures.append('Missing CSS asset: ' + asset)
    for path, parser in pages.items():
        relative = '/' + str(path.relative_to(output))
        for target in parser.links:
            part = urlsplit(target)
            if part.scheme or part.netloc: continue
            if part.path.startswith('/'):
                if base and not (part.path == base or part.path.startswith(base + '/')):
                    failures.append(f'{relative}: link escapes base path: {target}'); continue
                clean = part.path[len(base):].lstrip('/')
                resolved = output / unquote(clean)
            else:
                resolved = path.parent / unquote(part.path) if part.path else path
            if resolved.is_dir(): resolved /= 'index.html'
            if not resolved.is_file(): failures.append(f'{relative}: missing target: {target}'); continue
            if part.fragment and resolved.suffix == '.html' and unquote(part.fragment) not in pages[resolved].ids:
                failures.append(f'{relative}: missing anchor: {target}')
    home = (output / 'index.html').read_text()
    if 'github:OmarDardery/nixlab#install' not in home: failures.append('Missing canonical installation command')
    index = json.loads((output / 'search-index.json').read_text())
    for item in index:
        if not item['title'] or not item['text']: failures.append('Empty search entry')
        target = output / item['url'][len(base):].lstrip('/') / 'index.html'
        if not target.is_file(): failures.append('Search target missing: ' + item['url'])
    if failures: raise ValueError('\n'.join(failures))
    print(f'Checked {len(pages)} HTML pages, internal links/anchors/assets and {len(index)} search entries ({base or "/"}).')


def build(output, base):
    if output.exists():
        if any(output.iterdir()) and not (output / '.nojekyll').is_file():
            raise ValueError('Refusing to replace a directory not owned by the website build: ' + str(output))
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(SOURCE / 'assets', output / 'assets')
    shared = ROOT / 'admin/static'
    for name in ('brand.css', 'favicon.svg'):
        shutil.copyfile(shared / name, output / 'assets' / name)
    shutil.copytree(shared / 'fonts', output / 'assets/fonts')
    (output / '.nojekyll').touch()
    (output / 'index.html').write_text((SOURCE / 'index.html').read_text().replace('@@BASE@@', base))
    sources = sorted((ROOT / 'docs').glob('*.md'))
    pages = {p.stem for p in sources} | {'index'}
    registered = {slug for _, items in GROUPS for slug, _ in items}
    if pages - registered: raise ValueError('Add new docs to navigation: ' + ', '.join(sorted(pages - registered)))
    search = []
    for source in sources:
        title, body, toc, plain = render(source, base)
        slug = source.stem
        description = DESCRIPTIONS.get(slug, plain[:165].strip() + ('…' if len(plain) > 165 else ''))
        destination = output / 'docs' / slug / 'index.html'; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(template({'BASE': base, 'TITLE': escape(title), 'DESCRIPTION': escape(description), 'CANONICAL': '/docs/' + slug + '/', 'NAV': navigation(base, slug, pages), 'BODY': body, 'TOC': toc, 'SOURCE': REPO + '/blob/main/docs/' + source.name}))
        search.append({'title': title, 'url': url(base, slug), 'text': plain})
    cards = ''.join(f'<a href="{url(base, slug)}"><h2>{escape(title)}</h2><p>{escape(DESCRIPTIONS[slug])}</p></a>' for slug, title in [('plain-nixos', 'Install Nixlab ↗'), ('troubleshooting', 'Troubleshoot ↗'), ('recovery', 'Back up & recover ↗'), ('commissioning', 'Verify your hardware ↗')])
    body = '<p>Start with a plain installed NixOS server. The default installer configures Incus, Cockpit and the Docker/Portainer VM, then verifies the enabled interfaces.</p><div class="docs-intro-grid">' + cards + '</div>'
    for title, items in GROUPS[1:]:
        body += '<h2>' + escape(title.title()) + '</h2><ul>' + ''.join(f'<li><a href="{url(base, slug)}">{escape(label)}</a></li>' for slug, label in items) + '</ul>'
    home = output / 'docs' / 'index.html'
    home.write_text(template({'BASE': base, 'TITLE': 'Read this before improvising.', 'DESCRIPTION': DESCRIPTIONS['index'], 'CANONICAL': '/docs/', 'NAV': navigation(base, 'index', pages), 'BODY': body, 'TOC': '<a href="' + url(base, 'plain-nixos') + '">Start with installation →</a>', 'SOURCE': REPO + '/tree/main/docs'}))
    (output / 'search-index.json').write_text(json.dumps(search, ensure_ascii=False))
    not_found = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>404 · Nixlab</title><link rel="stylesheet" href="{base}/assets/brand.css"><link rel="stylesheet" href="{base}/assets/style.css"></head><body><main class="not-found section-wrap"><p class="eyebrow">404 / UNDECLARED RESOURCE</p><h1>This page failed to evaluate.</h1><p>Or the link moved. Either way, a rebuild of your server will not help.</p><a class="button button-primary" href="{url(base)}">Back to the docs →</a></main></body></html>'
    (output / '404.html').write_text(not_found)
    urls = [DOMAIN + '/'] + [DOMAIN + '/docs/'] + [DOMAIN + '/docs/' + p.stem + '/' for p in sources]
    (output / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join('<url><loc>' + escape(link) + '</loc></url>' for link in urls) + '</urlset>')
    (output / 'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: ' + DOMAIN + '/sitemap.xml\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '_site')
    parser.add_argument('--base-path', default='')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    base = '/' + args.base_path.strip('/') if args.base_path.strip('/') else ''
    if not re.fullmatch(r'(?:/[A-Za-z0-9_.-]+)*', base) or '..' in base: parser.error('Use a simple URL base path')
    output = args.output.resolve()
    if output == ROOT or ROOT.is_relative_to(output) or output in (ROOT / 'docs', SOURCE) or output.is_relative_to(SOURCE): parser.error('Output must be a disposable build directory')
    build(output, base)
    if args.check: check(output, base)
