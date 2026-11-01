"""CIAN DOM contract. No Avito markers belong in this adapter."""
CONFIG = {
    'id': 'cian',
    'parserVersion': 'cian-v2-gallery-variants',
    'stability': ['[data-name="OfferTitleNew"]','[data-name="OfferTitle"]','[data-name="PriceInfo"]','[data-name="OfferDescription"]','[data-name="Description"]','[data-name="OfferSummaryInfoItem"]','[data-name="ObjectFactoidsItem"]','[data-name="FeaturesItem"]','[data-name="OfferFactItem"]','[data-name="UndergroundItem"]'],
    'metadataTokens': ["_cianConfig['frontend-offer-card']", '_cianConfig["frontend-offer-card"]'],
    'fields': ['[data-name="'+name+'"]' for name in (
        'OfferSummaryInfoItem','ObjectFactoidsItem','OfferFactItem','FeaturesItem',
        'OfferSummaryInfoGroup','OfferTitleNew','OfferTitle','OfferDescription',
        'Description','PriceInfo','GeneralInfoSection','Geo','Underground')]+['h1'],
    'gallery': ['[data-name="OfferGallery"]','[data-name="Gallery"]'],
    'sections': {
        'gallery': ['[data-name="OfferGallery"]','[data-name="Gallery"]'],
        'description': ['[data-name="OfferDescription"]','[data-name="Description"]'],
        'characteristics': ['[data-name="GeneralInfoSection"]','[data-name="FeaturesItem"]',
            '[data-name="OfferSummaryInfoGroup"]','[data-name="OfferFactItem"]','[data-name="ObjectFactoidsItem"]'],
    },
    'headings': {},
    'ogFallback': True,
    'strict': False,
}
