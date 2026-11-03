"""A candidate's OS lease proves activity; a leftover JSON file never does."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path


@contextmanager
def candidate_lease(folder):
    import fcntl
    folder=Path(folder)
    if folder.is_symlink():raise ValueError('Каталог кандидата не должен быть ссылкой')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/'training-active.lock'
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Этот кандидат уже обучается в другом процессе')
        yield
    finally:
        os.close(descriptor)


def activity(folder):
    import fcntl
    folder=Path(folder);path=folder/'training-active.lock'
    if folder.is_symlink() or path.is_symlink() or not path.is_file():return {'active':False,'measuredBy':'no-candidate-lease'}
    try:
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        try:
            try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'active':True,'measuredBy':'candidate-os-lease'}
            return {'active':False,'measuredBy':'candidate-os-lease'}
        finally:os.close(descriptor)
    except OSError:return {'active':False,'measuredBy':'unavailable'}


def with_candidate_lease(function):
    @wraps(function)
    def wrapped(args,*positional,**keywords):
        with candidate_lease(args.output):return function(args,*positional,**keywords)
    return wrapped
