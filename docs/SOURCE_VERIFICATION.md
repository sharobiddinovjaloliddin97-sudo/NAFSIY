# NAFSIY V0.9.5 — source verification

This version adds technical source identity and quotation checks. It does **not** certify religious correctness, interpretation, authenticity, or a personal ruling. NAFSIY is an educational assistant, not a mufti. Engineering review was performed on 2026-10-31; qualified scholarly review has not been performed.

## Provenance and permissions

The code-reviewed registry is [source_registry.py](../backend/ai/source_registry.py). `GET /api/v1/quran/sources` exposes its public metadata. Entries are frozen dataclasses; changes require a code review and supporting permission evidence. There is no runtime switch that makes an arbitrary publication approved. Neither an API response nor a model may supply licensing approval.

| Identifier | Content | State | Runtime behavior |
| --- | --- | --- | --- |
| `quran-uthmani` | Arabic Uthmani through AlQuran Cloud; Tanzil / Quran Academy curation attribution | `licensed` | The only reviewed Arabic edition; fetched on demand |
| `en.itani` | English, Talal Itani / ClearQuran | `licensed` | Attributed English text, unchanged quotations |
| `en.sahih` | Saheeh International | `permission_pending` | No text requests or RAG inclusion |
| `ru.kuliev` | Russian, Elmir Kuliev | `permission_pending` | No text requests or RAG inclusion |
| `uz.sodik` | Uzbek, Muhammad Sodiq Muhammad Yusuf | `permission_pending` | Metadata availability confirmed; no text downloaded |
| `tafsiri-hilol` | Tafsiri Hilol | `restricted` | No scraper, adapter, download, import or AI context |
| `ummah-hadith` | UmmahAPI Arabic/English hadith payload | `permission_pending` | Adapter retained and fixture-tested; live text retrieval and reader fail closed |

`disabled` is also supported for withdrawal or operational suspension. Unknown identifiers fail closed. Pending/restricted checks precede HTTP calls. A second registry check at the agent boundary prevents an adapter from injecting a disallowed source into model context. Environment variables cannot bypass these checks.

Evidence inspected:

- [AlQuran Cloud API documentation](https://alquran.cloud/api) specifies edition identifiers and reference-based retrieval. Its [provider terms](https://alquran.cloud/terms-and-conditions) describe Arabic reuse and require preservation of orthography and source attribution. These are provider claims; this task did not independently collate all 6,236 verses against a printed mushaf.
- [Tanzil text license](https://tanzil.net/docs/Text_License) requires unchanged text and attribution. Arabic display includes the curation credit and copyright/license notice. This license is not treated as permission for every translation.
- [ClearQuran's rights-holder download terms](https://clearquran.com/download/) identify CC BY-ND 4.0 and additional permissions, including commercial redistribution with unchanged wording and attribution. Older files may contain different notices; the current rights-holder page addresses that difference. The registry preserves the exact provider edition and credits the translator. Provider delivery is not an assurance that every byte matches the latest rights-holder release.
- [Tanzil translation terms](https://tanzil.net/trans/) limit their distribution grant and direct other uses to the translator/publisher. This is why API availability did not enable Uzbek or Russian text. Rights-holder review remains necessary for the intended NAFSIY use and jurisdictions.
- UmmahAPI's live collection metadata reported `fawazahmed0/hadith-api (via jsDelivr CDN)`. Its [upstream software license](https://github.com/fawazahmed0/hadith-api/blob/1/LICENSE) is Unlicense, while the [upstream documentation](https://github.com/fawazahmed0/hadith-api/blob/1/README.md) describes editions and multiple grades. Software licensing alone did not establish all underlying translation rights. A single existing-adapter sample was inspected for schema; no collection was imported or cached.

These decisions are an engineering allowlist, not legal advice or a substitute for a rights-holder/scholarly review. No Tafsiri Hilol content was accessed.

## Edition and language policy

`QURAN_ARABIC_EDITION=quran-uthmani` selects the reviewed original. An unreviewed value fails closed. The new default `QURAN_EDITION_EN=en.itani` applies when the variable is absent; live `.env` was not edited. A preexisting default such as `en.sahih` will produce an unavailable response until the operator deliberately selects an approved edition.

Arabic originals and English source quotations are enabled. Uzbek Latin/Cyrillic and Russian questions remain supported through query expansion and the planner, with licensed English/Arabic evidence. **Published Uzbek and Russian translations are unavailable**, not silently substituted. The reader disables these options. Machine explanations/paraphrases carry an unverified AI label and are not attributed to a published translator. For Arabic responses, only structured source quotations are accepted; free Arabic commentary is conservatively withheld by the prose guard.

Both the RAG adapters and the standalone Quran/Hadith reader services enforce permissions. The Quran reader validates edition, surah identity, complete ayah counts, and ordered verse boundaries. No vector database, embedding service, corpus import or extra model call was added.

## Identity and quotation algorithm

1. Validate Quran references against an explicit 114-surah Hafs/Kufan ayah-count table (6,236 total), including each surah's last valid and first invalid ayah. `[112:5]` is invalid even though five is below the Quran-wide maximum.
2. Resolve provider results only when their surah/ayah and edition metadata agree with the requested reference. Conflicting duplicate texts for an edition are rejected. Search results must name the requested approved edition. A translation-only fallback is explicitly translation-only and cannot authorize an Arabic quote.
3. Assign a stable identity from provider, edition, numbering system and reference. Store a separate SHA-256 content version over source identity, selected text and grade fields. This hash makes the observed content version traceable; it is **not** an independently trusted corpus signature or a permanent provider revision pin.
4. Ask Groq for a strict JSON object containing `explanation` and `quotations`. Each quotation names `source_id`, `reference`, `language`, `edition`, and `text`. Extra fields such as model-provided `verified=true` or role overrides are rejected.
5. Commentary requires citations from the retrieved set. Citation existence establishes a reference link only. Direct quote delimiters, Arabic text, explicit edition attribution, grading/narrator assertions, raw HTML, code and remote links cannot bypass the structured quotation path. This is a conservative syntax/phrase guard, not a multilingual semantic proof.
6. A quotation must match the **complete** selected source field. Only Unicode NFC canonical equivalence and CRLF-to-LF equivalence are accepted. Diacritics, tatweel, punctuation, words, whitespace and verse boundaries are not stripped, repaired or transliterated. Excerpts and truncated source records cannot be claimed as verified full quotations. Missing languages, wrong attribution, conflicting metadata, ambiguous numbering or changed content fail closed.
7. Render the provider field itself, with the correct Arabic or translation attribution. A Markdown fence longer than any run inside the source prevents source text from creating active Markdown images/links/HTML. The model's proposed quotation is never the rendered authority.
8. Reject the whole generated answer on a failed check and return a localized warning with no cited sources. No validation retry consumes another provider attempt. Empty/uncited coverage is withheld rather than filled from model memory.

The public/persisted source JSON adds source ID, content hash, editions, translation language, numbering system, attribution, license state, `verification_status`, `grading_status`, and `quotation_status`. `quotation_status=exact_source_match` describes text equality only. The existing `verified` field deliberately remains **false**. Old chat metadata remains readable and gains no retrospective verification. Send, persistence and completed replay preserve the new metadata.

The current API, JWT/CSRF, idempotency, request quotas, cost reservations, two provider phases, lease fencing and message transaction remain in force. Source metadata uses existing JSON/Text columns; no migration was needed. The schema head remains `c4d8e2a9106f`. Existing completed responses still replay; this task did not rewrite private conversations or retroactively remove their text.

## Hadith limits

The existing UmmahAPI sample supplies collection, `hadithnumber`, Arabic, English and a bare `grade`. It supplies no named grading authority, established edition or guaranteed cross-edition numbering system. The adapter preserves the provider number (including decimal subdivisions), collection, text language, bare grade and any supplied grade-authority/grade-list metadata. Missing numbering is explicitly `ummahapi-provider-numbering-unspecified`.

Conflicting collection/number aliases, text aliases, grading metadata or duplicate records are marked as conflicts rather than silently choosing authoritative content. Different numbering systems are never converted or merged. No narrator is invented. No Sunnah.com or other hadith link is synthesized from an UmmahAPI number. License links are provenance links, not claims of a verified canonical hadith destination.

`grading_not_verified` is the current state even when the provider says Sahih. A collection title is not a grading certificate. There is no trusted grading authority integration in this release, so the model may not turn a bare API grade into an authenticity statement. Rights and provenance must be resolved before live hadith content is enabled; named source evidence and conflict policy need separate scholarly approval.

## Uzbek retrieval and evaluation

[multilingual.py](../backend/ai/multilingual.py) normalizes query-only apostrophe variants, case and whitespace, and transliterates Uzbek Cyrillic for a small topic map. It never normalizes sacred source text this way. English/Russian terms are also recognized. Expansion is bounded to three hints and does not override exact references. Unsupported vocabulary, suffixes, dialects and ambiguous transliterations still depend on the planner or produce no evidence.

The benchmark is [source_benchmark.json](../evaluations/source_benchmark.json); measured output is [source_results.json](../evaluations/source_results.json). It contains 30 guard decisions, 20 multilingual retrieval queries and five scholar-referral questions. Unit tests add exhaustive Quran boundaries and adversarial source/numbering/permissions cases. The labels were reviewed for engineering consistency during implementation. **They have not been reviewed by a qualified Islamic scholar or an independent Uzbek linguist.** A representative held-out review set is required before public release.

| Metric | Observed result | Scope |
| --- | --- | --- |
| Citation precision among accepted benchmark answers | 100% | Structured synthetic guard fixtures; not factual correctness |
| Unsupported quotation rejection | 100% | Labeled wrong-text/reference/edition/diacritic/boundary fixtures |
| Unsupported-answer rejection | 100% | 23 negative guard cases |
| False-positive rejection | 0% | Seven positive, schema-compliant fixtures; real prose over-rejection remains unmeasured |
| Scholar referral | 5/5 | Explicit fatwa/divorce keywords; not comprehensive personal-ruling detection |
| Guard decision latency | median 0.027 ms; max 0.550 ms | Local run, excludes network/model |

Live public-source GETs used the actual adapter and `en.itani`, with no Groq calls or database writes. Five topic queries per language were run in both raw baseline and expanded modes (40 runs). The selected topics are patience, prayer, knowledge, mercy and fasting. These overlap the implemented glossary; the results show its narrow benefit, not general semantic understanding. Knowledge keyword hits outside the chosen relevance judgment are counted as misses. Provider failures/empty results remain in the denominator; precision@3 always divides by three.

| Query language | Baseline precision@3 | Expanded precision@3 | Expanded recall | Expanded median latency |
| --- | --- | --- | --- | --- |
| Uzbek Latin | 0% | 86.7% | 100% | 899 ms |
| Uzbek Cyrillic | 0% | 86.7% | 100% | 899 ms |
| English | 86.7% | 86.7% | 100% | 924 ms |
| Russian | 0% | 86.7% | 100% | 893 ms |

This compares raw queries with deterministic expansion; it does not measure the previous Groq planner end to end. No benchmark claim is made about hadith relevance while its license is pending. No evidence here justifies a new vector service.

Actual benchmark AI cost was **$0**: zero Groq calls. The configured maximum two-call pipeline reservation is **$0.045056** using the illustrative configured prices and token ceilings; it is not a provider price quote or hard currency cap. End-to-end Groq latency, output quality and production token usage were not measured. The JSON report retains timings and individual reference results without source text or private questions.

Reproduce from the project root:

```powershell
.\.venv\Scripts\python.exe evaluations\evaluate_sources.py
.\.venv\Scripts\python.exe evaluations\evaluate_sources.py --live --output evaluations\source_results.json
```

`--live` means public source GETs only. It does not invoke Groq or touch a database. Unit/regression and browser commands/results are recorded in [PRELAUNCH.md](PRELAUNCH.md).

Full regression commands (PostgreSQL tests explicitly select and verify the separate test URL):

```powershell
$env:NAFSIY_RUN_POSTGRES_TESTS='1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

Final verification, 2026-10-31: **173/173 backend tests PASS**, zero skips, **456.300 seconds** (156 local/unit/regression and 17 real Neon PostgreSQL tests). The total includes 25 source-verification tests and the new metadata persistence/replay regression. Frontend lint/build and both Edge browser suites PASS. Alembic test head/schema comparison PASS, with no migration executed. Earlier development runs are superseded by this full run against the stable benchmark.

Additional public-provider checks passed: all 114 ayah counts match the provider's surah metadata (6,236 total), and the real reader service returned surah 112 with four ordered verses and the requested `en.itani` edition/attribution. These checks made no database or Groq calls and do not replace independent corpus collation.

For the browser suites, start the isolated fixture and built frontend as described in [AI_OPERATIONS.md](AI_OPERATIONS.md), then run:

```powershell
$env:NAFSIY_PLAYWRIGHT_PATH="$env:TEMP\nafsiy-browser-check\node_modules\playwright"
node tests\browser\ai_operations.cjs
node tests\browser\source_verification.cjs
```

## Remaining launch work

- Obtain documented permission for Tafsiri Hilol and desired Uzbek/Russian/hadith translations. Keep them blocked until reviewed; do not confuse API access with reuse rights.
- Have scholars review source editions, Quran numbering, interpretation boundaries, authenticity evidence and personal-ruling referral behavior. A deterministic keyword referral is not a universal fatwa detector.
- Independently collate provider text against a reviewed, version-pinned corpus and plan source update/revocation handling. Current hashes record what was retrieved, not whether the provider was correct.
- Review a representative, held-out Uzbek Latin/Cyrillic/Russian dataset, including suffixes, ambiguous terms, unavailable sources and legitimate prose likely to be over-rejected.
- Treat prompt-injection tests as defenses around schema, roles and output validation, not proof of immunity. A model can still produce unsupported commentary without obvious quote/grade markers. Matching citations cannot prove interpretation or factual entailment.
- Verify production licensing scope, rights-holder notices, pricing, collector/alerts, source outages/rate limits and output JSON truncation behavior. Longer structured prompts still fit the existing input/output budget controls.
- Drain old workers at rollout. Preserve the outstanding auth/proxy, billing/reconciliation, backup and deployment tasks in PRELAUNCH.

No deployment, primary migration, live secret/config change, commit or push was performed.

## V0.9.5 changed files

This task preserved the preexisting uncommitted V0.9.4.4 implementation. The V0.9.5-specific changes are:

- `.env.example`
- `backend/ai/agent.py`, `citation_guard.py`, `retriever.py`
- New `backend/ai/source_registry.py`, `references.py`, `multilingual.py`, `source_metadata.py`
- `backend/api/ai.py`, `user_data.py`, `quran.py`
- `backend/core/config.py`
- `backend/services/quran_service.py`, `hadith_service.py`
- `frontend/src/app/page.tsx`, `globals.css`, `frontend/src/hooks/useChatSessions.ts`
- `frontend/src/components/QuranReader.tsx`, `HadithReader.tsx`
- `tests/test_ai.py`, `test_retriever.py`, `test_ai_send.py`, new `test_source_verification.py`
- `tests/browser_server.py`, new `tests/browser/source_verification.cjs`
- New `evaluations/source_benchmark.json`, `evaluate_sources.py`, `source_results.json`
- `docs/SOURCE_VERIFICATION.md`, `docs/PRELAUNCH.md`

An unrelated `.gitignore` whitespace change appeared during the session and was left untouched.
