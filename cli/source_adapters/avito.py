"""Avito DOM contract. Repeated item-params IDs are sections, not one field."""
CONFIG = {
    'id': 'avito',
    'parserVersion': 'avito-v1',
    'metadataTokens': [],
    'fields': ['[data-marker="'+name+'"]' for name in (
        'item-view/title-info','item-view/item-price','item-view/item-price-container',
        'item-view/main-gallery','item-view/item-params','item-view/item-description',
        'item-view/location','item-view/item-address','item-map-wrapper','realty-usp/desktop-chips')]
        +['#item-price','[itemprop="price"]','[itemprop="address"]'],
    'gallery': ['[data-marker="item-view/main-gallery"]'],
    'sections': {
        'gallery': ['[data-marker="item-view/main-gallery"]'],
        'description': ['[data-marker="item-view/item-description"]'],
        'characteristics': ['[data-marker="item-view/item-params"]','[data-marker="item-view/location"]'],
    },
    'headings': {'Особенности':'characteristics','О квартире':'characteristics',
        'Условия аренды':'characteristics','Правила':'characteristics',
        'О доме':'characteristics','Расположение':'characteristics','Описание':'description'},
    'ogFallback': False,
    'strict': True,
}
