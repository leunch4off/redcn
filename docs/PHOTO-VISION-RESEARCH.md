# Распознавание мебели и техники на Mac mini M2, 8 ГБ

Первичные источники проверены 30 октября 2026 года. Ниже разделены опубликованные возможности, инженерные рекомендации и измерения. Модель не обучается на наших объявлениях: используются готовые открытые веса, ограниченный запрос и проверка результата. Размер загрузки не равен памяти, необходимой при анализе фото.

## Выбранный провайдер

Для первого локального запуска выбран **native ARM64 Ollama и `qwen3-vl:2b-instruct-q4_K_M`**. Реестр Ollama указывает вход Text+Image и около **1,9 ГБ весов**. Официальная карточка Qwen3-VL-2B-Instruct имеет лицензию **Apache 2.0**; runtime Ollama — MIT. Страница семейства требует Ollama 0.12.7 или новее. Тег закреплён явно: `latest` у этого семейства означает более крупную 8B-модель. [1–4]

Это практичный выбор для проекта: существующий Python-сервер может обращаться по loopback HTTP к отдельному ARM-процессу. Архитектура x86 самого веб-сервера не заставляет ARM Ollama работать через Rosetta. Ollama официально поддерживает macOS 14+; для Apple M-series доступны CPU и GPU. Отдельные PyTorch/MPS-зависимости основному серверу не нужны. Вариант через Docker Desktop на Mac не даёт документированной GPU-поддержки Ollama. [5–6]

Объём в 1,9 ГБ нельзя выдавать за потребление всего процесса. Дополнительно расходуют память активации/рабочие буферы vision encoder, контекст/KV cache, runtime/Metal, macOS, Chrome и сайт. Это не означает отдельной обязательной загрузки ещё одного encoder. Опубликованного подтверждения «эта конфигурация с нашим набором фото всегда укладывается в 8 ГБ и работает за N секунд» не найдено. Включение по умолчанию должно опираться на измерение реальных фото.

## Сопоставление локальных вариантов

| Вариант | Вход и результат | Загрузка, лицензия и Mac | Структура ответа и применение |
| --- | --- | --- | --- |
| Ollama + Qwen3-VL-2B-Instruct Q4_K_M | Изображение и текст → текст | Около 1,9 ГБ весов; Qwen Apache 2.0, Ollama MIT; macOS14+, native M-series CPU/GPU. [1–5] | REST `/api/chat` принимает base64-изображения в `messages[].images`; `format` принимает JSON Schema. Основной кандидат для видимой мебели/техники. Форма JSON не гарантирует истинность признаков. [7–9] |
| MLX-VLM + Qwen3-VL-2B-Instruct-4bit | Изображение и текст → текст | Опубликованная mlx-community-конвертация около 1,78 ГБ; исходная Qwen Apache2. MLX-VLM MIT, Python>=3.10; MLX требует native Apple Silicon Python и macOS>=14. [10–13] | Текущий авторский README подтверждает schema-constrained `json_schema` в `/v1/chat/completions` и `/v1/responses`, включая multimodal. Speculative decoding с ним не поддерживается. Рабочая версия зависимостей требует закрепления и теста. [10] |
| llama.cpp + совместимая VLM/GGUF | Изображения через мультимодальный runtime | Runtime MIT; Metal на macOS включён по умолчанию. Обычно нужны GGUF-модель **и соответствующий `mmproj`**, размер и лицензия зависят от выбранных весов. [14–17] | `llama-server` документирует image input и JSON Schema/grammar. Совместимость конкретной пары model/projector проверяется по supported models; существование обычного текстового GGUF не доказывает vision. Отдельный стек без преимущества для первой интеграции. |
| SmolVLM-500M-Instruct через MLX-VLM | Изображение+текст → текст; английская instruction-модель | Apache2. Авторы указывают **1,23 ГБ GPU RAM для одного изображения** и показывают MLX-запуск. Это не замер всей памяти M2. [18–19] | Конкретный более лёгкий резерв, если 2B окажется тяжёлой. Строгую схему обеспечивает совместимый runtime; качество именно для шкафов, кухонной техники и пианино ещё проверяется. |
| SmolVLM-256M-Instruct | Изображение+текст → текст | Apache2; оригинальный `model.safetensors` около 513 МБ. Семейство имеет Transformers/MLX/ONNX-варианты. [19–20] | Минимальный по размеру кандидат; несколько image patches и runtime добавляют память. Меньший файл не доказывает достаточную точность описи квартиры. |
| Microsoft Florence-2-base | Задача `<OD>` → подписи и bounding boxes; также OCR/captioning | MIT, около 0,23B параметров. Официальный пример выбирает CPU float32 без CUDA и использует `trust_remote_code=True`. [21] | Полезен для координат отдельных объектов. В изученном первичном источнике нет готового подтверждённого MPS-бенчмарка. Нужны проверка remote code, pin ревизии и отдельный Mac-тест. |
| Apple Vision / Core ML | Системная классификация/OCR; пользовательская Core ML-модель может возвращать найденные объекты | Встроенные Apple framework; не отдельный open-source набор весов VLM. [22–24] | Whole-image классификатор не даёт полную опись всех предметов и их количества. Для детекции нужен соответствующий Core ML detector. Остаётся полезным лёгким резервом и OCR-инструментом. |

## Контракт распознавания для объявления

Рекомендуемая схема результата — ограниченный список наблюдений: разрешённый `label`, привязка к конкретному фото, короткое видимое основание и количество только для различимых предметов. Неизвестные категории, дополнительные ключи, повреждённый JSON и слишком длинные ответы отклоняются. В списке проекта нужны шкафы, рабочие/обеденные столы, стулья, кровати, диваны, вытяжка, духовка, варочная панель, холодильник, микроволновка, посудомойка, стиральная машина, телевизор, компьютер, пианино и раковина.

Это политика нашего приложения, а не обещание модели. Наличие встроенной духовки не подтверждает скрытую варочную панель, холодильник или микроволновку. Два снимка кухни могут показывать один и тот же стул: количества между фото нельзя просто складывать. Закрытые шкафы не доказывают наличие гардеробной; высоту потолков, площадь, число отдельных комнат, разрешение животных и условия регистрации по внешнему виду выводить нельзя. Самооценка VLM не является калиброванной вероятностью. Сомнительные признаки сохраняются кандидатами для проверки.

Для Ollama предлагается один снимок за запрос и одна последовательная очередь: `stream:false`, `temperature:0`, `num_ctx:4096`, `num_predict:256–384`. В запрос передаются схема и её краткое объяснение; `think` задаётся только при подтверждённой поддержке модели через `/api/show`. На первом измерении — `keep_alive:0`, затем можно проверить короткое удержание между фото. Метрики `load_duration`, `prompt_eval_duration`, `eval_duration` и `total_duration` позволяют отделять холодную загрузку от самого анализа. Это стартовые ограничения, не измеренные оптимальные настройки. [8–9,25]

Сетевой адрес фиксируется literal loopback-конфигурацией сервера; пользовательские URL в качестве AI endpoint не принимаются. Клиент не следует redirect и не использует proxy для локального API. Кэш ключуется хэшем копии фото, точным model tag/digest и версией prompt/schema. Исходные фотографии не перезаписываются. Тайм-аут должен прекращать текущий запрос, сохраняя понятную причину; повторная попытка не должна создавать несколько параллельных моделей.

## Установка без изменения системных приложений

Для воспроизводимого пилота найден официальный stable **Ollama v0.40.13**, опубликованный 28 октября 2026. Значения прочитаны из первичного GitHub Releases API, а не из поискового описания. Релиз сообщает `immutable:false`, поэтому digest закрепляется отдельно. [26]

| Поле | Значение |
| --- | --- |
| ZIP | `https://github.com/ollama/ollama/releases/download/v0.40.13/Ollama-darwin.zip` |
| Размер ZIP | 229773910 байт, около219,13 MiB; без модели |
| SHA-256 ZIP | `da3189f87034d224beb0708d77b95180bc03872f6e269d59bd888a216055c58a` |
| Приватный каталог приложения | `~/.cache/redcn/tools/ollama/0.40.13/Ollama.app` |
| Встроенный CLI | `Ollama.app/Contents/Resources/ollama` |
| Приватный каталог весов | `~/.cache/redcn/tools/ollama/models` |
| Выделенный API | `http://127.0.0.1:11435` |

Официальная документация допускает расположение .app вне Applications и вызов встроенного CLI по пути. Установочный helper проверяет размер/SHA-256 до распаковки; затем Apple `codesign --verify --deep --strict`, Developer ID, Gatekeeper `spctl --assess` и наличие ARM64 до запуска. Ошибка проверки останавливает установку. Нельзя снимать quarantine, переподписывать приложение или отключать Gatekeeper ради продолжения. Публично документированную текущую константу TeamIdentifier найти не удалось: фактическое значение надо записать из проверенного bundle. [5,27–28]

Собственный `ollama serve` получает `OLLAMA_NO_CLOUD=1`, `OLLAMA_HOST=127.0.0.1:11435`, `OLLAMA_MODELS`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_MAX_QUEUE=2`, `OLLAMA_CONTEXT_LENGTH=4096`, `OLLAMA_KEEP_ALIVE=0`. Эти ограничения относятся к отдельному процессу. HOME и глобальные настройки пользователя не изменяются; чужой Ollama на11434 не останавливается. Обычная первая инициализация `serve` создаёт `~/.ollama/id_ed25519` и `.pub`, если ключей нет: это видно в исходном коде, даже при local-only режиме. Содержимое существующих ключей не требуется читать или выводить. [6,29]

Установка, запуск, загрузка модели и анализ — отдельные этапы. Статус/остановка собственного PID не должны зависеть от свободных гигабайтов. Скачать приложение — не значит проверить качество модели; доступность `/api/show` — не значит уже распознана квартира.

## Внешние API с бесплатным тарифом

Проверенные варианты имеют квоты и условия использования. Ни один не подтверждён как неограниченный бесплатный API для московского сервера. Отправка фото во внешний сервис должна быть отдельным явно выбранным режимом; договоры и другие вложения не передаются вместе с фото автоматически.

| Сервис | Подтверждённые условия | Ограничение для проекта |
| --- | --- | --- |
| Hugging Face Inference Providers | Бесплатный аккаунт получает $0,10 в месяц; сумма может меняться. [30] | Нужны доступная image/VLM-модель и конкретный провайдер; не каждая карточка HF развёрнута как hosted API. Кредит не обеспечивает постоянный поток фото. |
| Gemini Developer API | Free tier есть у определённых моделей; поддерживается JSON Schema. На странице цен free tier помечен как используемый для улучшения продуктов. [31,33] | В опубликованном списке поддерживаемых регионов Россия отсутствует. Платная оплата сама по себе не доказывает снятие региональных ограничений. Общую универсальную квоту RPM/RPD для всех моделей заявлять нельзя. [32] |
| OpenRouter `:free` | Официально20 запросов/мин,50/день; неудачные попытки учитываются; повышение до1000/день связано с покупкой $10 кредитов. [34] | Нужен ключ; image input и schema зависят от модели/endpoint. Состав бесплатных моделей меняется, data retention зависит от провайдера и настроек. Доступность для региона пользователя здесь не подтверждена. [35] |

Ограничение JSON формализует ответ, но не устраняет ошибки восприятия. Облако не требуется для основного локального сценария.

## Проверка перед автоматическим заполнением

Следующий протокол — наш инженерный критерий, а не опубликованный бенчмарк. На10–20 настоящих фотографиях нужно отдельно измерить холодный и тёплый запрос, p50/p95 времени, ошибки/тайм-ауты, память процесса/Metal и давление общей памяти одновременно с сервером и обычной работой браузера. Снимки должны включать кухню, спальню, рабочее место, похожие шкафы/двери, скрытую технику и повторяющиеся ракурсы.

Для каждого предмета сравниваются ложные добавления, пропуски и правильность видимого количества. Приоритет — не добавлять в объявление отсутствующую технику. Без эталонной ручной разметки нельзя сообщать процент точности. При нехватке памяти первым резервом остаётся SmolVLM-500M через отдельное native MLX-окружение, затем существующий Apple Vision и ручное подтверждение. В этом документе время/память фактического запуска пока не заявлены; фактический receipt установки хранится отдельно от исследования.

## Первичные источники

1. Ollama registry, точные теги/размеры: https://registry.ollama.com/library/qwen3-vl/tags
2. Ollama, семейство и требуемая версия: https://ollama.com/library/qwen3-vl
3. Qwen, официальный model card/license: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
4. Ollama MIT license: https://github.com/ollama/ollama/blob/main/LICENSE
5. Ollama, macOS и путь CLI: https://docs.ollama.com/macos
6. Ollama FAQ, local-only, память/concurrency, model directory: https://docs.ollama.com/faq
7. Ollama vision input: https://docs.ollama.com/capabilities/vision
8. Ollama structured outputs, включая vision: https://docs.ollama.com/capabilities/structured-outputs
9. Ollama chat API/options/runtime metrics: https://docs.ollama.com/api/chat
10. MLX-VLM, авторский README и structured multimodal output: https://github.com/Blaizzy/mlx-vlm/blob/main/README.md
11. MLX-VLM Python requirement/license: https://github.com/Blaizzy/mlx-vlm/blob/main/pyproject.toml
12. MLX, официальная установка и native Python: https://ml-explore.github.io/mlx/build/html/install.html
13. MLX-community Qwen3-VL-2B 4-bit конвертация: https://huggingface.co/mlx-community/Qwen3-VL-2B-Instruct-4bit
14. llama.cpp multimodal model/projector: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
15. llama.cpp server image input/JSON Schema: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
16. llama.cpp Metal build: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
17. llama.cpp license: https://github.com/ggml-org/llama.cpp/blob/master/LICENSE
18. Hugging Face, SmolVLM-500M: https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct
19. Статья авторов SmolVLM с MLX-командой: https://huggingface.co/blog/smolervlm
20. SmolVLM-256M, оригинальные файлы: https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct/tree/main
21. Microsoft Florence-2-base: https://huggingface.co/microsoft/Florence-2-base
22. Apple Vision image classifier: https://developer.apple.com/documentation/coreml/classifying-images-with-vision-and-core-ml
23. Apple object detector: https://developer.apple.com/documentation/vision/recognizing-objects-in-live-capture
24. Apple CoreMLRequest: https://developer.apple.com/documentation/vision/coremlrequest
25. Ollama show/model capabilities: https://docs.ollama.com/api/show
26. GitHub release API, size/digest/published date: https://api.github.com/repos/ollama/ollama/releases/tags/v0.40.13
27. Apple Code Signing Tasks: https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html
28. Apple notarization issues: https://developer.apple.com/documentation/Security/resolving-common-notarization-issues
29. Ollama v0.40.13, initializeKeypair: https://github.com/ollama/ollama/blob/v0.40.13/cmd/cmd.go
30. Hugging Face pricing: https://huggingface.co/docs/inference-providers/en/pricing
31. Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
32. Gemini supported regions: https://ai.google.dev/gemini-api/docs/available-regions
33. Gemini structured output: https://ai.google.dev/gemini-api/docs/structured-output
34. OpenRouter free model limits: https://openrouter.ai/collections/free-models
35. OpenRouter provider logging: https://openrouter.ai/docs/guides/privacy/provider-logging
