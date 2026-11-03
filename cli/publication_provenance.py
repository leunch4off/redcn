"""Attribute each saved publication part to the operation that produced it.

A title-only model request is not evidence that the body was model-written.
The bounded digest below is an edit detector shared with the browser, not a
security checksum. Transactions continue to compare the complete old payload.
"""
import copy


PARTS = ('title', 'body', 'tags')
METADATA_KEYS = ('status', 'model', 'backend', 'styleId', 'variation', 'factsHash',
    'elapsedSeconds', 'fallback', 'warnings', 'ai', 'modelAccepted', 'textSource',
    'modelIssues', 'generationMode', 'modelContribution', 'factualTextSource',
    'contract', 'inventoryCount', 'photoInventoryCount', 'sourceContextCharacters',
    'sourceContextTruncated', 'part')


def text_digest(value):
    value = value if isinstance(value, str) else ''
    first, second = 2166136261, 2246822519
    for char in value:
        first = ((first ^ ord(char)) * 16777619) & 0xffffffff
        second = ((second ^ ord(char)) * 3266489917) & 0xffffffff
    return f'p1:{len(value)}:{first:08x}:{second:08x}'


def text_parts(card):
    titles = card.get('titles')
    return {'title': titles[0] if isinstance(titles, list) and titles and isinstance(titles[0], str) else str(card.get('title') or ''),
        'body': str(card.get('body') or ''), 'tags': str(card.get('tags') or '')}


def clean_metadata(value):
    if not isinstance(value, dict):
        return {}
    result = {key: copy.deepcopy(value[key]) for key in METADATA_KEYS if key in value}
    if isinstance(value.get('parts'), dict):
        rows = {}
        for part in PARTS:
            row = value['parts'].get(part)
            if not isinstance(row, dict):
                continue
            rows[part] = {key: copy.deepcopy(row[key]) for key in (*METADATA_KEYS, 'textDigest') if key in row}
        if rows:
            result['parts'] = rows
            result['provenanceVersion'] = 1
    if isinstance(value.get('appliedParts'), list):
        result['appliedParts'] = [part for part in PARTS if part in value['appliedParts']]
    return result


def metadata_for(card):
    fields = card.get('fields') if isinstance(card.get('fields'), dict) else {}
    return clean_metadata(card.get('aiAdvertisement') or fields.get('aiGeneration'))


def part_origin(card, part):
    if part not in PARTS:
        raise ValueError('Неизвестная часть объявления')
    info = card.get('templateInfo') if isinstance(card.get('templateInfo'), dict) else {}
    if part in (info.get('manualParts') or []):
        return 'manual'
    metadata = metadata_for(card)
    row = (metadata.get('parts') or {}).get(part)
    if isinstance(row, dict):
        if row.get('textDigest') != text_digest(text_parts(card)[part]):
            return 'manual'
        if row.get('generationMode') == 'manual':
            return 'manual'
        if row.get('generationMode') == 'assistant-editorial' and row.get('textSource') == 'assistant-reviewed':
            return 'assistant-reviewed'
        if row.get('generationMode') == 'individual-model-prose' and row.get('modelAccepted') is True and row.get('fallback') is not True:
            return 'individual'
        if row.get('textSource') == 'rules' or row.get('generationMode') == 'explicit-template':
            return 'rules'
    # Old metadata is scoped, but is not bound to the current text. Never use
    # it to overwrite an old publication or certify a current training target.
    scope = metadata.get('part', 'all')
    if scope in (part, 'all') and metadata.get('generationMode') == 'individual-model-prose' and metadata.get('modelAccepted') is True:
        return 'legacy-model' if part != 'tags' else 'rules'
    marker = {'title': 'titleId', 'body': 'descriptionId', 'tags': 'tagId'}[part]
    if info.get(marker) or part == 'body' and isinstance(info.get('copyEngine'), dict):
        return 'rules-unverified'
    return 'unknown'


def bind_generation(card, preview, *, previous=None, parts=None):
    """Bind only the newly applied parts, preserving other valid attribution."""
    result = copy.deepcopy(card)
    metadata = clean_metadata(preview)
    selected = parts or (PARTS if metadata.get('part', 'all') == 'all' else (metadata['part'],))
    selected = tuple(part for part in PARTS if part in selected)
    old = metadata_for(previous or card)
    rows = copy.deepcopy(old.get('parts') or {})
    values = text_parts(result)
    for part in selected:
        row = {key: copy.deepcopy(metadata[key]) for key in METADATA_KEYS if key in metadata}
        row.update(part=part, textDigest=text_digest(values[part]))
        if part == 'tags':
            row.update(textSource='rules', modelAccepted=False,
                generationMode='factual-tags', modelContribution='none')
        elif row.get('generationMode') == 'individual-model-prose':
            row['modelContribution'] = part+'-prose'
        rows[part] = row
    metadata.update(parts=rows, appliedParts=list(selected), provenanceVersion=1)
    if metadata.get('generationMode') == 'individual-model-prose':
        metadata['modelContribution'] = ('title-and-body-prose' if {'title', 'body'} <= set(selected)
            else next((part+'-prose' for part in selected if part != 'tags'), 'none'))
    result['aiAdvertisement'] = metadata
    result.setdefault('fields', {})['aiGeneration'] = copy.deepcopy(metadata)
    return result


def bind_manual(card, part, *, previous=None):
    metadata = {'part': part, 'status': 'saved', 'generationMode': 'manual',
        'textSource': 'operator', 'modelAccepted': False, 'modelContribution': 'none'}
    result = bind_generation(card, metadata, previous=previous, parts=(part,))
    # Tags edited by a person are not an automatic factual tag selection.
    result['aiAdvertisement']['parts'][part].update(metadata)
    result['fields']['aiGeneration'] = copy.deepcopy(result['aiAdvertisement'])
    return result
