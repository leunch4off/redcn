"""Shared card document routes; caller authenticates before dispatch."""
import re
from urllib.parse import quote


def _download_header(name):
    return {'Content-Disposition': 'attachment; filename="redcn-document"; filename*=UTF-8\'\'' + quote(name, safe='')}


def dispatch(directory, path, data=None, *, namespace='local', browser_channel='chrome'):
    """Return (status, value, MIME, headers), or None for unrelated routes."""
    import card_assets
    prefix='/api/'+namespace+'/'
    def asset(item):return {**item,'url':prefix+'exports/'+item['id']}
    def archive_view(result):
        card_id=result['result_id']
        return {**result,'attachments':[{**item,'url':prefix+'card-attachments/'+card_id+'/'+item['id']} for item in result['attachments']]}
    def json_result(value):return 200,value,'application/json; charset=utf-8',{}
    if path=='design':
        return json_result(card_assets.load_design(directory) if data is None else card_assets.save_design(directory,data.get('template')))
    match=re.fullmatch(r'card-archive/([a-f0-9]{32})',path)
    if match:
        card_id=match[1]
        if data is None:return json_result(archive_view(card_assets.archive(directory,card_id)))
        action=data.get('action','update')
        if action=='update':result=card_assets.update_archive(directory,card_id,{key:data[key] for key in ('status','notes') if key in data})
        elif action=='attach':result=card_assets.add_attachment(directory,card_id,data)
        elif action=='remove':result=card_assets.remove_attachment(directory,card_id,data.get('attachment_id'))
        else:raise ValueError('Неверное действие с архивом карточки')
        return json_result(archive_view(result))
    match=re.fullmatch(r'card-attachments/([a-f0-9]{32})/([a-f0-9]{32,64})',path)
    if match and data is None:
        content,mime,name=card_assets.attachment_asset(directory,match[1],match[2])
        return 200,content,mime,_download_header(name)
    match=re.fullmatch(r'card-exports/([a-f0-9]{32})',path)
    if match and data is None:return json_result({'exports':[asset(item) for item in card_assets.list_exports(directory,match[1])]})
    if path=='card-export' and data is not None:
        if data.get('kind') in ('intro','outro'):
            result=card_assets.create_image_export(directory,data.get('result_id'),data.get('data'),data.get('kind'))
        else:result=card_assets.create_export(directory,data.get('result_id'),data.get('html'),data.get('kind'),browser_channel=browser_channel)
        return json_result(asset(result))
    match=re.fullmatch(r'exports/([a-f0-9]{32,64})',path)
    if match and data is None:
        content,mime,name=card_assets.export_asset(directory,match[1])
        return 200,content,mime,_download_header(name)
    if path=='listing-map' and data is not None:
        from listing_map import map_image
        return json_result(map_image(directory,data.get('latitude'),data.get('longitude'),data.get('zoom',15)))
    return None


def request_limit(path):
    if re.fullmatch(r'card-archive/[a-f0-9]{32}',path):return 56_100_000
    if path=='card-export':return 14_100_000
    return None
