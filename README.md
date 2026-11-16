# LKG 2.0 Explorer

**Explorador de la legislación española publicada en el BOE · prototipo del Ontology Engineering Group (UPM).**

A web explorer and first version of the **Legal Knowledge Graph 2.0**: all ~26,000 Spanish laws in the open
[legalize-es](https://github.com/legalize-dev/legalize-es) repository (national and regional), ordered by the
hierarchy of legal norms, linked to each other and described as ELI linked data.

**Live page:** https://nsr223.github.io/lkg2/

## What it does

- **Search** by title, number or subject, accent-insensitive; also in English through EuroVoc labels.
- **Hierarchy of norms**: Constitution → organic law → law → decrees with the force of law → regulations → orders and other provisions, with counts and filters; status (in force, repealed, expired) and jurisdiction (national or each region).
- **Each law**: rank, dates, status, issuing body, ELI identifier, consolidated or original text, PDFs (consolidated and original BOE issue), and its **links to other laws** (cites, amends, repeals, amended by, repealed by, …; about 146,000 links).
- **Full text** with a jump to any article.
- **Article-by-article history** (for all 12,443 consolidated laws: 8,823 national and 3,620 regional, about 44,800 versions): read a law as it stood on any date, see every version of an article with the reform that introduced it, and the words each reform changed.
- **EuroVoc**: BOE subjects linked to EuroVoc concepts (about 1,550 subjects, 77% of subject uses; = same, ≈ close, ⊂ broader).
- **Linked data** for each law (JSON-LD and Turtle): the BOE's own ELI metadata plus what LKG 2.0 adds (status, hierarchy level, links between laws, EuroVoc, article versions).
- Spanish and English interface.

## Data and sources

| Data | Source | Notes |
|---|---|---|
| Laws, metadata, texts, links, versions | [legalize-es](https://github.com/legalize-dev/legalize-es), built from the consolidated texts of the Agencia Estatal Boletín Oficial del Estado ([boe.es](https://www.boe.es)) | Reuse under the BOE's [conditions](https://www.boe.es/informacion/aviso_legal/index.php) (attribution required). Texts for information only; the official version is the BOE. |
| Official ELI metadata | The `eli_metadata` field of each legalize-es file (published by the BOE) | Kept without loss; repetition removed (see `scripts/eli_extract.py`). |
| EuroVoc | [EuroVoc](https://op.europa.eu/en/web/eu-vocabularies/dataset/-/resource?uri=http://publications.europa.eu/resource/dataset/eurovoc), EU Publications Office (`eurovoc_in_skos_core_concepts`) | © European Union. Matches made automatically from labels, plus reviewed matches for the most-used subjects (`scripts/eurovoc_reviewed.tsv`). |

About the texts: 12,443 laws have a **consolidated** text (amendments built in, so they have a version history);
13,505 have the **original** text only (their amendments are separate laws).

## Files

```
index.html                 the page (no build step; it loads data/ on demand)
data/laws.json             one row per law (compact: repeated strings are stored once in "dict")
data/refs.json             all links between laws
data/eurovoc.json          BOE subject → EuroVoc concept
data/eli/*.json            the BOE's official ELI metadata, 16 files
data/hist/*.json.gz        article-by-article versions, packed in 16 gzip files (+ index.json)
scripts/                   everything needed to rebuild data/ from legalize-es and EuroVoc
server/                    the local Q&A service (FastAPI): /api/qa, and the page itself
config.yaml                which model the Q&A service uses
```

## Q&A service (runs on your computer)

The **Q&A** tab answers questions about Spanish law through a small local server. On the GitHub Pages site the tab only
explains how to start it; to ask questions, run the server:

```bash
pip install -r server/requirements.txt
python server/app.py              # open http://localhost:8000  (API docs at /docs)
```

The server listens on `localhost` only. It serves the page and its data (nothing else from the folder) and the API.

**Choosing the model** (`config.yaml`, read on every question, so no restart is needed):

- `mode: local` uses [Ollama](https://ollama.com) on your computer: install it, run `ollama pull qwen2.5:7b`
  (or `llama3.1:8b`, `gemma2:9b`, …) and set `local.model`.
- `mode: frontier` uses a hosted model: set `frontier.provider` (`anthropic`, `openai` or `gemini`) and
  `frontier.model`, and put the API key in a `.env` file (copy `.env.example`). `.env` is ignored by git.

**API**

`GET /api/qa` tells you what the service is set up to use:
`{"service": "lkg2-qa", "scenario": "plain-llm", "mode": "local", "provider": "ollama", "model": "qwen2.5:7b"}`

`POST /api/qa` with `{"question": "...", "mode": "local", "scenario": "rag"}` (`mode` and `scenario` are optional and
override `config.yaml`) returns `{"question", "answer", "scenario", "mode", "provider", "model", "seconds", "sources",
"search_terms"}`. Errors return status 503 with a
`detail` that says what to fix (Ollama not running, model not pulled, missing API key).

Three more optional fields, used for evaluation runs:
- `"model": "llama3.1:8b"` uses another model of the same provider for this question, without editing `config.yaml`.
- `"history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]` gives the earlier turns of a
  conversation; `question` is then the latest user message. In `rag`, the search uses everything the user has said.
- `"references": true` asks the model to end with a `REFERENCIAS:` list (law, article and paragraph), so answers
  with and without retrieval can be checked in the same way.

`GET /api/questions` returns the demo questions shown in the tab. They are read from `private/questions.json`, which
git ignores, so they stay off the web and out of future model training data. Ask the authors for the file.

```bash
curl -X POST http://localhost:8000/api/qa -H 'Content-Type: application/json' \
     -d '{"question": "¿Cuánta fianza hay que dar al alquilar una vivienda?"}'
python server/ask_all.py                        # the demo questions; answers saved to answers.jsonl
python server/ask_all.py --scenario rag --out answers_rag.jsonl   # the same with LKG 2.0 retrieval, to compare
python server/ask_all.py --questions path/to/benchmark.jsonl --out path/to/results.jsonl   # any question file
python server/ask_all.py --questions path/to/benchmark.jsonl --scenario rag --model llama3.1:8b --out path/to/results.jsonl
```

**Two scenarios** (`scenario` in `config.yaml`, or `"scenario"` in the request; the Q&A tab has a selector):

- `plain-llm` (scenario 1): the model answers from its own knowledge.
- `rag` (scenario 2): LKG 2.0 is searched first and the model answers from what is found: the articles in force that
  best match the question, plus a record of any law the question names (status, repealed by, amended by, from the links
  in the graph). The model is told to cite them as [1], [2]…; the response lists them in `sources` with their ELI.
  Optionally the model first suggests legal search terms (`rag.rewrite_query`), so "alquiler" also finds "arrendamiento",
  and the laws that probably govern the question (`rag.suggest_laws`), which are then searched article by article.
  A model often knows which law applies even when it does not remember the current text of the article.
  Long articles are cut to the paragraphs that best match the search (`rag.max_chars`), with a note saying which
  numbered paragraph the cut starts in.

`rag` needs the search database, `search.db` (about 1 GB, not in git). Download the ready-made one from the
[release](https://github.com/nsr223/lkg2/releases/tag/search-db-2026-11-15) (about 700 MB):

```bash
python scripts/get_search_db.py
```

or build it yourself (about 4 minutes):

```bash
git clone --depth 1 https://github.com/legalize-dev/legalize-es legalize-es     # texts of laws with no consolidated version
python3 scripts/build_search.py data/laws.json data/hist legalize-es search.db  # or "-" instead of legalize-es: consolidated laws only
```

It holds every law in force (`--all` adds repealed ones): about 10,000 consolidated laws from `data/hist` and 11,000 with
only an original text, split into about 537,000 articles, searched with SQLite full-text search (no extra software).

## Rebuilding the data

```bash
git clone --depth 1 https://github.com/legalize-dev/legalize-es legalize-es          # current texts
git clone --filter=blob:none https://github.com/legalize-dev/legalize-es legalize-hist  # history (for versions)

python3 scripts/build_index.py  legalize-es data          # laws.json, refs.json (+ excerpts/, used only in the Claude version)
python3 scripts/eli_extract.py  legalize-es data          # eli/
python3 scripts/eurovoc_parse.py eurovoc_in_skos_core_concepts.rdf eurovoc_min.json
python3 scripts/eurovoc_map.py  eurovoc_min.json data/laws.json data/eurovoc.json scripts/eurovoc_reviewed.tsv
python3 scripts/article_history.py legalize-hist data/laws.json work BOE-A-2015-11430 ...   # or @file-with-ids
python3 scripts/history_pack.py data/laws.json work/history data/hist
```

`scripts/eurovoc_candidates.py` lists EuroVoc candidates for unmatched subjects, to review by hand.
`scripts/make_standalone.py` makes this page from the version used inside Claude.

## Status and limits

- **Prototype.** Names under `urn:lkg2:` (ranks, agents, article units and their versions) are provisional until a permanent namespace is chosen.
- The ELI modelling of article versions (`eli:LegalResourceSubdivision`, `eli:has_member`, `eli:version_date`) is a first proposal and needs review.
- Article history works at the level of whole articles and provisions, not paragraphs or letters, and is as complete as the legalize-es history.
- EuroVoc matches can contain errors; a check of 100 random automatic matches found 96 clearly right, 3 debatable and 1 wrong (since fixed).

## Credits and licence

Noah Rose, Ontology Engineering Group, Universidad Politécnica de Madrid.

The code (`index.html`, `scripts/`) is under the [MIT licence](LICENSE). The data in `data/` comes from the sources above and stays under their own conditions.

---

<sub>Built with help from Claude (AI assistant).</sub>
