"""User-session server supervisor. No browser window, credentials, or network changes.

The editor always binds its existing loopback port. If a normal launcher already
owns that exact application/storage, wait for it rather than creating another
server. An unrelated process on that port is never terminated or bypassed.
"""
import argparse
import errno
from pathlib import Path
import signal
import threading
import time


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message, flush=True)


def run_service(directory, port=8765, phone_port=8767, *, stop_event=None,
                retry_seconds=5, backend=None):
    if (type(port) is not int or type(phone_port) is not int
            or not 1 <= port <= 65535 or not 1 <= phone_port <= 65535
            or port == phone_port):
        raise ValueError('Нужны два разных порта от 1 до 65535')
    if not 0 < retry_seconds <= 60:
        raise ValueError('Повтор подключения: от 0 до 60 секунд')
    if backend is None:
        import local_server as backend
    directory = Path(directory).expanduser().resolve()
    stopping = stop_event if stop_event is not None else threading.Event()
    active = None
    previous_signal = {}

    def stop(*_args):
        stopping.set()
        # shutdown waits for serve_forever and must not run on that thread.
        if active is not None:
            threading.Thread(target=active.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_signal[sig] = signal.signal(sig, stop)
    last_status = None
    reused_phone_enabled = False

    def report(status, message):
        nonlocal last_status
        if last_status != status:
            log(message)
            last_status = status

    try:
        while not stopping.is_set():
            try:
                active = backend.create_server(directory, port, 'chrome')
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    report('bind-error', 'Не удалось открыть локальный сервер; повтор через несколько секунд.')
                    stopping.wait(retry_seconds)
                    continue
                if backend.matching_server(directory, port, 'chrome'):
                    report('existing', 'Уже работает этот redcn. Ожидаем его завершения, второй сервер не запускаем.')
                    if not reused_phone_enabled:
                        try:
                            backend.enable_existing_phone(port, phone_port)
                            reused_phone_enabled = True
                        except (OSError, ValueError):
                            report('existing-phone-error', 'Редактор работает, телефон пока недоступен; повтор подключения.')
                else:
                    reused_phone_enabled = False
                    report('busy', 'Порт редактора занят другим приложением. Ждём освобождения порта.')
                stopping.wait(retry_seconds)
                continue
            reused_phone_enabled = False
            try:
                active.phone_relay.enable(phone_port)
                report('ready', f'Сервер запущен. Редактор: http://127.0.0.1:{port}/; телефон: порт {phone_port}.')
                if not stopping.is_set():
                    active.serve_forever(poll_interval=.25)
            except (OSError, ValueError):
                report('phone-error', 'Не удалось запустить телефонный сервер; повтор через несколько секунд.')
            finally:
                # Closes only the listener and tunnel owned by this process.
                active.server_close()
                active = None
            if not stopping.is_set():
                stopping.wait(retry_seconds)
    finally:
        if active is not None:
            active.server_close()
        for sig, previous in previous_signal.items():
            signal.signal(sig, previous)
        log('Фоновый сервер остановлен.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Фоновый сервер redcn без открытия браузера')
    parser.add_argument('--cache-dir', type=Path, default=Path.home()/'.cache'/'redcn')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--phone-port', type=int, default=8767)
    args = parser.parse_args(argv)
    return run_service(args.cache_dir, args.port, args.phone_port)


if __name__ == '__main__':
    raise SystemExit(main())
