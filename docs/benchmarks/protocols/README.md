These are exact bytes of executed research protocols retained for provenance.
They are not additional runtime entrypoints. To replay a snapshot, place it at
its original `scripts/evaluate-rental-heldout.py` path in a disposable checkout
with the recorded corpus/checker/runtime versions.

`evaluate-rental-heldout-05-v26.py` SHA-256:
`50bda66221d6fe04da895aaa8f807a792d6e3429537dfced1e4210cf60358f19`.
It produced the first paired 0.5B base/balanced measurements. The later runner
adds only the hash-pinned 1.5B base candidate branch; the six cases and quality
checker stay frozen for comparison. No 0.5B adapter can attach to 1.5B.


The three `*-v26-frozen.py` files preserve the exact checker/helper/runtime-validation sources used by the original 0.5B and 1.5B runs. They are provenance snapshots, not imports in the production runtime. Replay requires a temporary checkout with those bytes restored under their original `cli/` names. Later fee-scope guard fixes are post-hoc and do not change the original report headlines.
