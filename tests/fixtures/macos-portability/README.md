# Public synthetic Mac portability fixture

All content is generated for testing and dedicated to CC0-1.0: solid-colour PNG
pixels, a synthetic PDF marker, a generated triangle OBJ, synthetic Chinese text,
settings, dictionary and training examples. There are no real listings, source
photographs, contracts, credentials, cookies or operator cache contents.

`workspace.zip` and `source.json` are exported by
`scripts/portable-mac-proof.py export --require-macos --origin operator-mac` on
the operator's actual Mac. The exporter always constructs its own temporary
workspace. Its SHA, runtime OS and archive SHA are recorded in `source.json`.
A Linux export must never be relabelled as Mac evidence.

The `mac-portability` job restores these exact bytes in a new `macos-15` hosted
Mac workspace. It compares card relationships, original/cropped photo bytes,
attachment bytes, templates, versions, dictionary and learning counts, and emits
a public verification artifact. A passing Linux unit test alone is not the
second-Mac proof. The first accepted CI run and receipt must be recorded in the
release report.

GitHub's current runner specifications:
https://docs.github.com/en/actions/reference/runners/github-hosted-runners
