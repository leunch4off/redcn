"""Public saved-search contracts; these never load full listing pages."""
from urllib.parse import urlsplit


def platform_for_url(value):
    try:host=(urlsplit(value).hostname or '').lower()
    except (TypeError,ValueError):return None
    if host in ('cian.ru','www.cian.ru') or host.endswith('.cian.ru'):return 'cian'
    if host in ('avito.ru','www.avito.ru'):return 'avito'
    return None
