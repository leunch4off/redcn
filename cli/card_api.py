"""Shared card document routes; caller authenticates before dispatch."""
import re
from urllib.parse import quote


def _download_header(name):
    return {'Content-Disposition': 'attachment; filename="redcn-document"; filename*=UTF-8\'\'' + quote(name, safe='')}


def dispatch(directory, path, data=None, *, namespace='local', browser_channel='chrome', actor=None):
    """Return (status, value, MIME, headers), or None for unrelated routes."""
    from photo_instance_api import dispatch as instance_dispatch
    instances = instance_dispatch(directory, path, data, namespace=namespace,
                                  actor=actor or ('Mac owner' if namespace == 'local' else 'paired editor'))
    if instances:
        return instances
    import card_assets
    prefix='/api/'+namespace+'/'
    def asset(item):return {**item,'url':prefix+'exports/'+item['id']}
    def archive_view(result):
        card_id=result['result_id']
        return {**result,'attachments':[{**item,'url':prefix+'card-attachments/'+card_id+'/'+item['id']} for item in result['attachments']]}
    def json_result(value):return 200,value,'application/json; charset=utf-8',{}
    if path=='photo-duplicates' and data is None:
        import photo_store
        return json_result(photo_store.exact_photo_duplicates(directory))
    match=re.fullmatch(r'dossier-backup/([a-f0-9]{32})',path)
    if match and data is None:
        from dossier_backup import export_private
        return 200,export_private(directory,match[1]),'application/zip',_download_header('redcn-private-'+match[1][:8]+'.zip')
    if path=='dossier-import' and data is not None:
        import base64,binascii
        from dossier_backup import MAX_BUNDLE,restore_private
        encoded=data.get('data')
        if set(data)!={'data'} or not isinstance(encoded,str) or len(encoded)>((MAX_BUNDLE+2)//3)*4:raise ValueError('Нужна приватная ZIP-копия до 128 МБ')
        try:content=base64.b64decode(encoded,validate=True)
        except (binascii.Error,ValueError) as error:raise ValueError('Неверное содержимое ZIP') from error
        return json_result(restore_private(directory,content))
    match=re.fullmatch(r'dossier-(options|design|client)/([a-f0-9]{32})',path)
    if match:
        from dossier_backup import client_options,get_restored_design,export_client
        if match[1]=='options' and data is None:return json_result(client_options(directory,match[2]))
        if match[1]=='design' and data is None:return json_result(get_restored_design(directory,match[2]))
        if match[1]=='client' and data is not None:return 200,export_client(directory,match[2],data),'application/zip',_download_header('ciliev-client-'+match[2][:8]+'.zip')
    if path=='design':
        try:
            if data is None:
                from phone_relay import load_profile
                rules=load_profile(directory).get('rules',{})
                personal={key:value for key,value in rules.items() if isinstance(key,str) and isinstance(value,str)} if isinstance(rules,dict) else {}
                return json_result({**card_assets.load_design(directory),'rules':personal})
            return json_result(card_assets.save_design(directory,data.get('template'),**({'expected_updated':data['expectedUpdated']} if 'expectedUpdated' in data else {})))
        except card_assets.DesignConflict as error:
            return 409,{'error':str(error)},'application/json; charset=utf-8',{}
    if path=='design-images' and data is not None:
        return json_result(card_assets.add_design_image(directory,data))
    match=re.fullmatch(r'design-images/([a-f0-9]{32})',path)
    if match and data is None:
        return json_result(card_assets.design_image(directory,match[1]))
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
            result=card_assets.create_image_export(directory,data.get('result_id'),data.get('data'),data.get('kind'),publication_policy_version=data.get('publicationPolicyVersion'),template=data.get('template'))
        else:result=card_assets.create_export(directory,data.get('result_id'),data.get('html'),data.get('kind'),browser_channel=browser_channel,template=data.get('template'))
        return json_result(asset(result))
    match=re.fullmatch(r'exports/([a-f0-9]{32,64})',path)
    if match and data is None:
        content,mime,name=card_assets.export_asset(directory,match[1])
        return 200,content,mime,_download_header(name)
    if path=='listing-map' and data is not None:
        from listing_map import map_image
        return json_result(map_image(directory,data.get('latitude'),data.get('longitude'),data.get('zoom',15),style=data.get('style','classic'),pin_color=data.get('pinColor')))
    return None


def request_limit(path):
    if re.fullmatch(r'photo-instances/[a-f0-9]{32}', path):return 400_000
    if path=='dossier-import':return 179_000_000
    if re.fullmatch(r'dossier-client/[a-f0-9]{32}',path):return 16_000
    if re.fullmatch(r'card-archive/[a-f0-9]{32}',path):return 56_100_000
    if path=='card-export':return 45_100_000
    if path=='design-images':return 5_650_000
    if path=='design':return 300_000
    return None
