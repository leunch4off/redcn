"""Authenticated non-secret owner context for this browser's notification ledger."""
import hashlib
from pathlib import Path


def dispatch(directory,path,data=None):
    if path!='device-delivery/context':return None
    if data is not None:raise ValueError('Контекст уведомлений доступен только для чтения')
    root=Path(directory).resolve()
    return 200,{'version':1,'workspaceId':hashlib.sha256(str(root).encode()).hexdigest(),
        'channel':'this-browser','serverPush':False,'requiresExplicitOptIn':True},'application/json; charset=utf-8',{}
