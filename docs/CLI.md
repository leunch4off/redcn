# Python CLI

Нужны Python 3.10+ и Node.js 22+. Дополнительные Python-пакеты не нужны. Из корня репозитория:

```bash
python3 cli/redcn.py
python3 cli/redcn.py 'https://www.cian.ru/rent/flat/ID/'
python3 cli/redcn.py --input examples/listing.txt
python3 cli/redcn.py --input examples/listing.txt --data examples/fields.json --variant 1 --json --output output/post.json
python3 cli/redcn.py 'https://www.avito.ru/AD_PATH' --download-photos output/photos --json --output output/avito.json
python3 cli/redcn.py --batch examples/batch.json --download-photos output/batch-photos --output output/batch-results.json
```

Ссылки с ID и AD_PATH выше — заполнители; их нужно заменить настоящими ссылками. `--input` работает без сети. Для первого запуска начните с приложенного демонстрационного текста.

- `--data`: JSON проверенных полей карточки, перекрывает извлечённые значения.
- `--rules`: JSON-словарь RU→CN; `--prefs`: benefits, contact, brandTags.
- `--variant`: номер формулы заголовка/тегов; результат воспроизводим, факты не изменяются.
- `--json`: чистый JSON в stdout; `--output`: записать файл вместо stdout.
- `--cache-dir`: каталог SQLite (по умолчанию ~/.cache/redcn); `--refresh`: повторная загрузка.
- `--download-photos`: скачивать доступные OG/JSON-LD фото в папку с manifest.json. Ограничение 30 фото, 12 МБ на файл, JPEG/PNG/WebP.

## Манифест новых ссылок

```json
[
  {"url":"https://www.cian.ru/rent/flat/ID/", "seller":"owner"},
  {"url":"https://www.avito.ru/AD_PATH", "seller":"agent", "commission":50}
]
```

`owner` — собственник, `agent` — посредник. Комиссия — процент аренды, не сумма рублей. Программа принимает 0…50% включительно; неизвестный автор/процент не допускается в автоматический отбор. Эти поля проверяет оператор. Успешно обработанные URL попадают в seen, следующие запуски пропускают их. Ошибки не помечаются успешными. Одна ссылка может дополнительно иметь объект data с проверенными полями.

Это пакетная обработка подготовленного списка, а не работающий автоматический поиск по всем новым объявлениям площадок. Коннекторы выдачи и мониторинг перечислены отдельными задачами Roadmap.

## На сайте

Нажмите «Импорт результата Python / JSON», выберите файл одиночного результата с --json. Сайт восстановит поля, заголовки, описание, теги и доступные фото. Batch-результат — массив отчётов для обработки; его нельзя выдавать за одиночную карточку.

## Ошибки

При запрете источника или CAPTCHA используйте --input. Скрипт не импортирует браузерные cookies и не обходит защиту. stdout содержит результат, stderr — ошибку; код возврата 2 означает отказ. Запросы проверяют HTTPS, домен, публичный IP, редиректы и размер ответа. Фото с неизвестных CDN не скачиваются автоматически.
