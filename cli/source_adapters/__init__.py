"""Select a source contract by the validated URL; never mix platform selectors."""
from urllib.parse import urlsplit
from .cian import CONFIG as CIAN
from .avito import CONFIG as AVITO


def adapter_for(url):
    host=(urlsplit(url).hostname or '').lower()
    if host=='cian.ru' or host.endswith('.cian.ru'):return CIAN
    if host=='avito.ru' or host.endswith('.avito.ru'):return AVITO
    raise ValueError('Адаптер поддерживает только объявления ЦИАН и Авито')
