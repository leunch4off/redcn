"""Bounded publication model shared by persistent PDF and social-card layouts.

No node can supply CSS, a URL, executable markup or arbitrary image bytes. The
browser owns normalisation; this boundary validates its complete v2 output.
"""
import math
import re


SURFACES = ('pdfOverview', 'pdfGallery', 'pdfLocation', 'intro', 'outro')
BINDINGS = frozenset(('title', 'body', 'brandName', 'agency', 'tagline', 'agentName',
    'agentRole', 'contact', 'phone', 'email', 'footer', 'metro', 'metroLine', 'walk',
    'price', 'priceLarge', 'priceUnit', 'area', 'layout', 'floor', 'address', 'city',
    'features', 'service', 'introFacts', 'mapTitle', 'mapMetro', 'routesTitle',
    'route1Title', 'route1Time', 'route1Stops', 'route2Title', 'route2Time',
    'route2Stops', 'disclaimer', 'mapAttribution', 'facts', 'route1', 'route2',
    'map', 'logo', 'avatar', *(f'photo{index}' for index in range(1, 31)),
    *(f'fact{index}{suffix}' for index in range(1, 7) for suffix in ('Label', 'Value')),
    *(f'route{route}Stop{stop}' for route in (1, 2) for stop in (1, 2, 3))))
BRAND_KEYS = frozenset(('name', 'agency', 'tagline', 'agentName', 'agentRole', 'phone',
    'email', 'contact', 'footer', 'logoAssetId', 'avatarAssetId'))
THEME_KEYS = frozenset(('background', 'foreground', 'accent', 'muted', 'panel', 'line'))
NODE_KEYS = frozenset(('id', 'type', 'x', 'y', 'w', 'h', 'bind', 'text', 'photoIndex',
    'visible', 'locked', 'z', 'style'))
STYLE_KEYS = frozenset(('fontSize', 'color', 'fontFamily', 'align', 'opacity',
    'background', 'borderColor', 'borderWidth', 'radius', 'fontWeight', 'objectFit',
    'lineHeight'))
NODE_TYPES = frozenset(('text', 'image', 'rect', 'line', 'logo', 'avatar', 'map', 'route', 'facts'))
_ID = re.compile(r'[a-zA-Z0-9_-]{1,80}\Z')
_ASSET = re.compile(r'[a-f0-9]{32}\Z')
_COLOR = re.compile(r'#[a-fA-F0-9]{6}\Z')


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Неверные поля дизайна: '+label)


def _string(value, maximum, label):
    if not isinstance(value, str) or len(value)>maximum or '\x00' in value:
        raise ValueError('Неверный текст дизайна: '+label)


def _number(value, minimum, maximum, label, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum<=value<=maximum or integer and not isinstance(value, int):
        raise ValueError('Неверное число дизайна: '+label)


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError('Неверное значение дизайна: '+label)


def _color(value, label):
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise ValueError('Цвет дизайна должен иметь формат #RRGGBB: '+label)


def validate_publication_template(template):
    _object(template, ('version', 'preset', 'paper', 'orientation', 'brand', 'theme', 'surfaces'), 'template')
    if type(template['version']) is not int or template['version']!=2:
        raise ValueError('Нужна версия дизайна 2')
    _choice(template['preset'], ('ciliev-elite', 'ciliev-navy'), 'preset')
    _choice(template['paper'], ('A4', 'Letter'), 'paper')
    _choice(template['orientation'], ('landscape',), 'orientation')
    _object(template['brand'], BRAND_KEYS, 'brand')
    for key, value in template['brand'].items():
        _string(value, 1000 if key in ('contact', 'footer') else 500, 'brand.'+key)
        if key.endswith('AssetId') and value and not _ASSET.fullmatch(value):
            raise ValueError('Неверный ID изображения бренда')
    _object(template['theme'], ('pdf', 'social'), 'theme')
    for group, colors in template['theme'].items():
        _object(colors, THEME_KEYS, 'theme.'+group)
        for key, value in colors.items():
            _color(value, 'theme.'+group+'.'+key)
    _object(template['surfaces'], SURFACES, 'surfaces')
    for key, surface in template['surfaces'].items():
        _object(surface, ('id', 'width', 'height', 'background', 'nodes'), key)
        if surface['id']!=key:
            raise ValueError('ID страницы не совпадает с именем страницы')
        _number(surface['width'], 320, 4096, key+'.width')
        _number(surface['height'], 320, 4096, key+'.height')
        _color(surface['background'], key+'.background')
        nodes = surface['nodes']
        if not isinstance(nodes, list) or len(nodes)>80:
            raise ValueError('На странице допускается до 80 элементов')
        seen = set()
        for node in nodes:
            _object(node, NODE_KEYS, key+'.node')
            identifier = node['id']
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in seen:
                raise ValueError('Неверный или повторяющийся ID элемента')
            seen.add(identifier)
            _choice(node['type'], NODE_TYPES, 'node.type')
            _string(node['bind'], 80, 'node.bind')
            if node['bind'] and node['bind'] not in BINDINGS:
                raise ValueError('Неизвестная подстановка данных')
            _string(node['text'], 20000, 'node.text')
            _number(node['w'], 4, surface['width'], 'node.w')
            _number(node['h'], 1 if node['type']=='line' else 2, surface['height'], 'node.h')
            _number(node['x'], 0, surface['width']-node['w']+0.000001, 'node.x')
            _number(node['y'], 0, surface['height']-node['h']+0.000001, 'node.y')
            _number(node['photoIndex'], 0, 29, 'node.photoIndex', integer=True)
            _number(node['z'], -100, 100, 'node.z', integer=True)
            if type(node['visible']) is not bool or type(node['locked']) is not bool:
                raise ValueError('Видимость и блокировка элемента должны быть флагами')
            style = node['style']
            _object(style, STYLE_KEYS, 'node.style')
            for color in ('color', 'background', 'borderColor'):
                _color(style[color], 'node.style.'+color)
            for name, choices in (('fontFamily', ('sans', 'serif')), ('align', ('left', 'center', 'right')), ('objectFit', ('cover', 'contain'))):
                _choice(style[name], choices, 'node.style.'+name)
            if type(style['fontWeight']) is not int or style['fontWeight'] not in (400, 500, 600, 700, 800):
                raise ValueError('Неверная насыщенность шрифта')
            for name, minimum, maximum in (('fontSize', 10, 200), ('opacity', 0, 1), ('borderWidth', 0, 20), ('radius', 0, 400), ('lineHeight', 1, 2.4)):
                _number(style[name], minimum, maximum, 'node.style.'+name)
    return template
