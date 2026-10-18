# 사전 데이터(stdict.db) 유지보수

> CLAUDE.md에서 옮긴 **상세 참조**다(2026-10-18 재구성 — 서술은 원문 그대로 옮기고 절 제목만 붙였다). CLAUDE.md에는 이 영역의 불변식·금지선 요약만 남았다.
> 설계·동작을 바꾸면 **이 문서와 CLAUDE.md 요약을 함께** 고칠 것(메모리 `dead-code-and-stale-memory-hygiene`).

## Dictionary data maintenance

`data/stdict.db` (~1.64M words) has **two independently-updated halves**, and they are maintained by *different* scripts on purpose. Never rebuild one in a way that clobbers the other.

| Half | Source | Script | Cadence |
|---|---|---|---|
| `source='stdict'` (표준국어대사전, ~437K) | `spellcheck-ko/korean-dict-nikl` **`stdict/`** | CI builds `stdict_part.db` ([build_stdict_part.py](../build_stdict_part.py)) → [update_stdict.py](../update_stdict.py) merges | semiannual, automated |
| `source='opendict'` (우리말샘, ~1.21M) | official 전체 내려받기 export in `data/전체 내려받기_우리말샘_json_YYYYMMDD/` | [update_opendict.py](../update_opendict.py) | manual, whenever the user downloads a new export |

우리말샘 can't be automated — its official export sits behind a login. The GitHub mirror *can* be, which is why CI only handles 표준국어대사전. [setup_dict.py](../setup_dict.py) still does a full from-scratch build of both (`REPO_NIKL` — all three dictionaries live in that one repo; the standalone `-stdict`/`-krdict` repos have been dead since 2019). ⚠ It is a **bootstrap only**: it rebuilds `words`/`meta` from scratch, so it would drop `norm_map`, the meta provenance keys and the official-export 우리말샘 half — it therefore **refuses to touch an existing `stdict.db` without `--force`**, and after a build it points to `update_opendict.py` (which restores `norm_map`). The old `--krdict` mode, which pulled the dead 2019 repo and overwrote the DB with a 65K krdict-only table (and was advertised as a "small test"), was removed 2026-10-18. CI's `build_stdict_part.py` imports only its `REPO_NIKL`/`stream_github`.

⚠ **`norm_map` (12,750 rows, built by [build_norm_map.py](../build_norm_map.py) from the 우리말샘 export) lives in the same DB file and is easy to destroy.** `update_opendict.py` builds a fresh temp DB and swaps it in, which silently dropped `norm_map` — killing every 규범표기 correction (컨텐츠→콘텐츠) with no error at all. It now rebuilds `norm_map` from the same export and gates on a table-loss invariant. `update_stdict.py` avoids the class of bug entirely by writing in-place inside a transaction. Both print an invariant check; **run `eval/ai_goldset/run_goldset.py` after any DB update** — that's what caught it.

Cadence rationale (measured 2026-07-22): upstream mirror refreshes irregularly (~semiannual); the 7-year-stale stdict source cost **0 headwords** of coverage because the fresh 우리말샘 covered all 391 gaps; and the residual gap is already handled live by the two API fallbacks. Monthly/quarterly data releases would ship 131MB for near-zero gain.
