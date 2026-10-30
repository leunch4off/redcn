"""Create a suggested factual revision after cached photographs are analyzed."""
import copy

from job_progress import emit
import redcn


def photo_revision(directory, result, *, text='', rules=None, prefs=None, report=None, cancel=None):
    from listing_photo_inventory import analyze_listing_photos
    from photo_ai_settings import load_config
    config=load_config(directory)
    inventory=analyze_listing_photos(directory,result,report,cancel,max_seconds=config['maxSeconds'])
    result['photoInventory']=inventory
    if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
    if inventory['state']=='disabled':return None
    fields=copy.deepcopy(result.get('fields') or {})
    previous=set(fields.get('photoAutoFeatures') or [])
    found=set(inventory.get('features') or [])
    # Refresh automatic observations; explicit user confirmations and source
    # positives survive a missed detection in a later photograph pass.
    audit=fields.get('sourceAudit') if isinstance(fields.get('sourceAudit'),dict) else {}
    source_positive={row['id'] for row in audit.get('furniture',[]) or []
                     if isinstance(row,dict) and row.get('status')=='present' and 'id' in row}
    human=[row for row in fields.get('photoEvidence',[]) or [] if isinstance(row,dict) and row.get('status')=='confirmed']
    human_ids={row['id'] for row in human if 'id' in row}
    fields['features']=[feature for feature in fields.get('features',[])
                        if feature not in previous or feature in source_positive or feature in human_ids]
    fields['photoEvidence']=human+inventory.get('photoEvidence',[])
    fields['photoAutoFeatures']=sorted(found)
    edits=fields.get('photoEdits') if isinstance(fields.get('photoEdits'),dict) else {}
    observations=edits.get('observations') if isinstance(edits.get('observations'),dict) else {}
    for photo in inventory.get('photos',[]):
        if photo.get('status')!='review':continue
        old=observations.get(photo['sourceUrl'],{})
        same=old.get('analysis',{}).get('originalId')==photo.get('originalId')
        observations[photo['sourceUrl']]={'analysis':{'version':2,'status':'review','originalId':photo['originalId'],
            'provider':photo.get('provider'),'model':photo.get('model'),'engine':photo.get('engine'),
            'autoApply':config['autoApply'],'candidates':photo['candidates'],'unknownIds':photo.get('unknownIds',[])},
            'confirmed':old.get('confirmed',{}) if same else {},'rejected':old.get('rejected',{}) if same else {}}
    fields['photoEdits']={**edits,'observations':observations}
    if not previous and not found:
        return {key:(fields if key=='fields' else copy.deepcopy(result[key]))
                for key in ('fields','body','titles','tags') if key=='fields' or key in result}
    emit(report,'Составляем китайский текст по данным объявления и видимым предметам',stage='generate')
    try:
        revision=redcn.generate(text or fields.get('source',''),fields,rules,prefs)
    except (OSError,ValueError) as error:
        inventory.update(state='partial',textUpdated=False,message='Предметы сохранены; обновление текста не удалось. Повторите генерацию.')
        return None
    if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
    return revision
