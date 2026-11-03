# Проверенный GitHub → Mac → Sites pipeline

Для отдельно разрешённой публикации Sites порядок обязателен: полный CI точного SHA
→ установка и проверка этого SHA на локальном Mac → облачная публикация.
Обычный push создаёт проверяемый GitHub-артефакт; он не развёртывает Sites,
не подключает облачную копию и не создаёт расписание. Один зелёный CI не разрешает обновление
Sites. Разрешение пользователя на разработку не заменяется непроверенными
полями из скачанного артефакта.

Версия политики: `data/release-policy.json`. Репозиторий `leunch4off/redcn`,
ветка `feature/rcn-v8-knowledge-photos`, Site
`appgprj_6ae36aadbaac819184b0a832deae799e`. Существующая аудитория — только
владелец. Никаких ключей в этой инструкции или GitHub Actions secrets нет.

## Подготовка проверенного выпуска

`verification.yml` проверяет именно `pull_request.head.sha` или push SHA, а не
неявный merge ref. После Linux/Chrome/Python и восстановления публичной
синтетической копии на отдельном hosted Mac задание `sites-release-artifact`
пакует три файла: `release.json`, `source.tar.gz`, `build.tar.gz`. Используется
только чистое tracked-дерево Git и результат его детерминированной сборки;
кеш, ключи, библиотеки пользователя и модели туда не попадают.

`cli/release_evidence.py` и `scripts/verify-sites-release.py` проверяют:

- свежие ответы GitHub о полном успешном CI, всех заданиях с `run_id` того же запуска, репозитории,
  ветке и exact SHA, а не утверждения самого архива;
- creator владельца GitHub-комментария именно из PR #6 (`issue_url`) с локальной квитанцией и совпадение
  SHA/tree/CI-run; отсутствие квитанции запрещает публикацию;
- актуальность артефакта, размер и SHA-256 скачанного ZIP из GitHub API;
- границы распаковки, пути, отсутствие ссылок/дубликатов/runtime-файлов;
- Git tree SHA, заново рассчитанный из **всех байтов и executable modes**
  исходников; build Worker и manifest должны побайтно совпадать с source;
- постоянный project ID и необходимость приватной публикации.

## Локальная квитанция

После фактической установки root/оператор запускает на Mac:

```text
python scripts/make-local-attestation.py --repository-root <actual-repo> --source-sha <40hex> --source-tree <40hex> --run-id <successful-run-id> --deployment-receipt <private-deployment-receipt.json> --phone-receipt <private-local-phone-receipt.json>
```

Программа требует Darwin, чистый установленный Git, правильные локальные
квитанции и текущий health. Она выводит **только несекретные** metadata с
хешем приватной квитанции. Root размещает вывод собственным авторизованным
GitHub-коннектором одним комментарием в PR #6. Начало:

```text
redcn-local-verification-v1
{"version":1,...}
```

Pipeline проверяет `comment.user.login == leunch4off` из API. Git author,
поле `actor` внутри JSON или чужой комментарий такой квитанцией не являются.
Частные файлы квитанций, пути, код подключения и токены в комментарий не идут.

## Облачный исполнитель

По отдельному запросу владельца к существующему Site можно подключить одну
задачу после проверки текущих automations. Этот документ не является командой
создать или включить такую задачу. Она не устанавливает код на Mac и не читает его файлы.
Она публикует только уже установленный владельцем exact SHA.

1. Получить `get_site`; сохранить ID, owner-only аудиторию, bindings и
   текущую опубликованную версию. При неподдержанной/неизвестной аудитории
   остановиться без изменения доступа.
2. Через GitHub connector получить текущую голову разрешённой ветки/PR #6,
   полный run JSON, jobs и artifacts. Отдельно получить raw Git commit
   `GET https://api.github.com/repos/leunch4off/redcn/git/commits/<sha>` и
   независимо прочитать его tree SHA; tree из комментария или архива не
   является таким источником. Полный run доступен через
   `github.fetch` GET `https://api.github.com/repos/leunch4off/redcn/actions/runs/<id>`.
   Отдельный wrapper списка run возвращает только часть полей.
3. Получить комментарии PR #6. Выбрать последнюю owner-authored локальную
   квитанцию **для этого же** SHA/tree/run. Если её нет, код ещё не прошёл
   локальную установку: завершить без публикации и без повторного уведомления.
4. Если предыдущая owner-authored publication-квитанция соответствует этому
   SHA и **текущей** опубликованной Site version/source commit, завершить тихо.
   Одного совпадения managed source HEAD недостаточно: push ещё не deployment.
5. Скачать ровно связанный с CI artifact через native
   `github.download_workflow_artifact`. Временная ссылка/файл не является
   источником доверия; проверить SHA ZIP из свежего artifact JSON.
6. Передать свежие raw API объекты в `verify-sites-release.py`:

   ```text
   python scripts/verify-sites-release.py --evidence <api-evidence.json> --artifact <downloaded.zip> --output <new-checked-folder>
   ```

   evidence JSON содержит только `ci`, `commit`, `jobs`, `artifact`, `comment`,
   `source_sha`, `source_tree`, `branch`. Сначала используется доверенная
   установленная версия валидатора; скачанный код до проверки не исполняется.
7. Получить **свежий** native Sites source credential и существующий checkout
   этого project. Не создавать новый Site, не менять audience и bindings.
   Открытие и source preparation следуют Sites skill. Ключ только через stdin
   helper/память процесса; не записывать его в `.env`, Git или журналы.
8. Скопировать проверенные source bytes/modes в чистый managed checkout.
   Удалять разрешено лишь прежние tracked-файлы, отсутствующие в проверенном
   source. Не перезаписывать неизвестные файлы или конкурентные изменения.
   `git write-tree` обязан равняться проверенному GitHub source tree.
   Commit message содержит исходный GitHub SHA и ID локальной квитанции.
   Обычный push без force; при конфликте завершить без публикации.
9. Публиковать точный **pushed Sites commit SHA** и проверенный
   `site-deploy.tar.gz` через `save_version_and_deploy_private`. Не подменять
   Sites source commit GitHub SHA. Дождаться terminal succeeded, если native
   ответ pending/building/publishing; сохранить возвращённые IDs.
10. Проверить saved version source provenance. Записать одну несекретную
    owner-authored квитанцию в PR #6 с префиксом
    `redcn-sites-publication-v1` и полями projectId/sourceSha/sourceTree/ciRunId/
    localAttestationCommentId/sitesCommitSha/versionId/deploymentId/url.
    Повторная задача сверяет её с актуальным `get_site_version`, поэтому
    повторно тот же коммит не публикуется. Не отправлять ничего в Telegram,
    почту или другим людям.

Успешная регистрация расписания сама по себе не означает, что pipeline уже
публиковал. RCN-195 закрывается только после нового реального облачного
запуска этим способом и readback опубликованной версии. Пока такой запуск
не подтверждён, это готовая реализация и проверяемый протокол, не фиктивный
автодеплой.

## Короткий prompt подключаемой задачи

> Обновляй приватный redcn Site только уже установленным и проверенным на Mac
> коммитом из leunch4off/redcn, ветка feature/rcn-v8-knowledge-photos. Следуй
> data/release-policy.json и docs/VERIFIED-SITES-PIPELINE-v26.md из закреплённой
> проверенной версии. Проверь свежие GitHub CI, artifact digest и owner-authored
> локальную квитанцию PR #6; без любого доказательства не публикуй. Сохраняй
> project ID, приватную аудиторию и bindings, используй свежие native Sites
> credentials только в памяти. Проверяй exact source tree и pushed Sites SHA.
> Уже опубликованный SHA не повторяй. Mac/импорты/диагностическую кампанию не
> трогай, Telegram и почту не используй. Когда подходящих изменений нет,
> заверши без уведомления.

## Первичные документы

- GitHub runners, fresh VM и реальные macos-15:
  https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Workflow artifacts и digest:
  https://docs.github.com/en/actions/tutorials/store-and-share-data
- Workflow-run privilege boundary:
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

Actions не получает Sites credential. Artifact job имеет только `contents:
read`, а source/write/private publish выполняются действующим коннектором
владельца после независимой локальной квитанции.
