# Local Russian → Chinese translation

The product uses three different resources. Their counts and guarantees must not be mixed:

- **Authored rental rules:** 197 base terms expanded into 1,140 explicit forms, plus a separately sourced 405-row rental phrase corpus. Normalized duplicate keys collapse in the runtime count. These support safe structured clauses, furniture, amounts, conditions and dictionary overrides.
- **Open dictionary:** 89,171 Russian–Chinese sense pairs, 66,360 Russian keys and 34,473 attested inflected-form keys from Wiktionary through Kaikki. This is searchable, attributed lookup data, with no claim that every sense has been manually reviewed. It is not 200,000 translated sentences. See [OPEN-RU-ZH-LEXICON.md](OPEN-RU-ZH-LEXICON.md).
- **Local neural preview:** optional Russian → English → Chinese inference for prose outside the phrase rules. A pivot can change meaning. Every result requires review; it never updates extracted facts or saves a card by itself.

The open dictionary is loaded only when searched/explicitly requested. It is not embedded in the initial page bundle. Blindly concatenating word senses is not used as a sentence translator.

## Install on the Mac server

From the repository, with an existing Python interpreter:

```sh
.venv/bin/python scripts/setup-translation-runtime.py
```

This creates **only** `.venv-translate`, using native ARM Python on Apple Silicon. Its `bin/python` launcher preserves `arch -arm64` even when the web server runs under x86 emulation. The application's existing `.venv` is untouched. Packages are CTranslate2, SentencePiece and pure-Python OpenCC plus their small runtime dependencies; Torch, Stanza and Argos Translate's Python package are not installed.

The installer downloads approximately 227 MB of official Argos packages and retains only inference files and their original notices under `~/.cache/redcn/translation-models`. `--runtime DIR`, `--models DIR` and `REDCN_TRANSLATION_MODELS` allow custom paths. It checks the official package index, fixed expected download size, archive paths and language/version metadata. The index does not publish hashes: SHA-256 is calculated and recorded **after TLS download**, not represented as an independently published upstream hash.

```sh
.venv-translate/bin/python cli/nmt_worker.py --status
printf '%s' '{"text":"Сдаётся квартира. Аренда 80000 рублей.","rules":{"Москва":"莫斯科"}}' | .venv-translate/bin/python cli/nmt_worker.py
```

`--status` avoids loading the native libraries or model weights. `available:true` means the dependencies can be found and required model files exist. Inference can still fail and must be handled as a preview failure. Input is bounded to 5,000 source characters and 1 MB JSON. Each model uses CPU int8 inference with two threads. The API limits worker time and falls back to the phrase preview on timeout; speed depends on the Mac and source length.

Output includes `text`, `backend:'nmt-pivot'`, `route:['ru','en','zh']`, `needsReview:true`, `warnings`, `checks` and `elapsedSeconds`. It reports missing/extra numeric values, possible lost negatives/conditions and proper names needing review. These are conservative checks, not proof of semantic equivalence. Exact supplied glossary matches are protected and restored once; if the model changes a protected marker, the worker translates only unprotected fragments and stitches the exact glossary values back, with a coherence-review warning. Numeric checks recognize Chinese 万/千/亿 amounts and common Chinese count words. A bounded table translates exact child/pet permission clauses before NMT; uncertain clauses stay in Russian with a review warning. This guard overrides personal term rules for those whole source conditions.

## Model sources and license notices

The official index is <https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json>.

| Package | Official download | Upstream notice |
|---|---|---|
| Russian → English 1.9 | <https://argos-net.com/v1/translate-ru_en-1_9.argosmodel> | README credits Aleksey Kutashov and lists training corpus notices. The inspected package does **not** clearly state one overall model license. The README includes mixed corpus terms, including QED's research-use notice. Preserve the original notice; do not claim the software license covers these weights. |
| English → Chinese 1.9 | <https://argos-net.com/v1/translate-en_zh-1_9.argosmodel> | README credits Jörg Tiedemann and Santhosh Thottingal and explicitly states the original OPUS model is **CC-BY 4.0**. |

These packages are downloaded directly from the official upstream source for local inference. They are not committed, bundled into the website, or redistributed by this repository. Both original `README.md` files and `metadata.json` are retained next to the model weights. The installer writes `redcn-models.json` with source URLs, observed hashes and the license distinction. Argos software's MIT/CC0 notice must not be substituted for the model-weight notices.

The worker uses the public CTranslate2 `Translator` and SentencePiece interfaces, without importing Argos's Stanza-based sentence splitter. It preserves paragraph separators and limits sentence-sized inference chunks. Changing model versions requires checking tokenizer layout, package metadata, notices and output quality again.
