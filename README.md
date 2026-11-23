# LKG 2.0 Explorer

**Explorador de la legislación española publicada en el BOE · prototipo del Ontology Engineering Group (UPM).**

A web explorer and first version of the **Legal Knowledge Graph 2.0**: all ~26,000 Spanish laws in the open
[legalize-es](https://github.com/legalize-dev/legalize-es) repository (national and regional), ordered by the
hierarchy of legal norms, linked to each other and described as ELI linked data.

**Live page:** https://nsr223.github.io/lkg2/

## Quick start (Q&A service on your computer)

```bash
git clone https://github.com/nsr223/lkg2.git && cd lkg2
pip install -r server/requirements.txt
python3 scripts/build_search.py data/laws.json data/hist - search.db   # search database, about 90 seconds
ollama pull qwen2.5:7b                                                 # needs Ollama: https://ollama.com
python server/app.py                                                   # then open http://localhost:8000
```

Choose the scenario in the Q&A tab (model alone, with LKG 2.0 search, or with the graph) and ask a question.
This search database covers the ~10,000 consolidated laws; the full one, with laws that only have their original
text, is described below. Optional: search by meaning, with `ollama pull bge-m3` and then
`python scripts/build_vectors.py search.db --jur es` (a few hours; can be stopped and resumed).
Settings are in `config.example.yaml`; to change them, copy it to `config.yaml` and edit the copy.

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
config.example.yaml        default settings (copy to config.yaml for your own; config.yaml is not committed)
```

## Q&A service (runs on your computer)

The **Q&A** tab answers questions about Spanish law through a small local server. On the GitHub Pages site the tab only
explains how to start it; to ask questions, run the server:

```bash
pip install -r server/requirements.txt
python server/app.py              # open http://localhost:8000  (API docs at /docs)
```

The server listens on `localhost` only. It serves the page and its data (nothing else from the folder) and the API.

**Choosing the model**: copy `config.example.yaml` to `config.yaml` and edit that (it is read on every question, so no
restart is needed, and git ignores it). Without a `config.yaml`, the server uses the defaults in `config.example.yaml`.

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

**Four scenarios** (`scenario` in `config.yaml`, or `"scenario"` in the request; the Q&A tab has a selector):

- `plain-llm` (scenario 1): the model answers from its own knowledge.
- `rag` (scenario 2, text only): the articles are searched first and the model answers from what is found: the
  articles in force that best match the question, each with only its law's title and the article's name. Nothing from
  the knowledge graph is added. The model is told to cite them as [1], [2]…; the response lists them in `sources` with their ELI.
  Optionally the model first suggests legal search terms (`rag.rewrite_query`), so "alquiler" also finds "arrendamiento",
  and the laws that probably govern the question (`rag.suggest_laws`), which are then searched article by article.
  A model often knows which law applies even when it does not remember the current text of the article.
  Long articles are cut to the paragraphs that best match the search (`rag.max_chars`), with a note saying which
  numbered paragraph the cut starts in.
- `rag-graph` (scenario 3a, text + knowledge graph in the prompt): the same search, plus what LKG 2.0's graph adds: a record of each
  law the question names (rank, scope, status, amended by, repealed by), each article's scope, status and the date its
  current version took effect, and articles reached by following links from the best results (see below).
  Scenarios 2 and 3a use the same articles, prompt and model, so the difference between them measures the graph.
- `graph-tools` (scenario 3b, the model queries the graph): instead of receiving a fixed set of articles, the model
  calls four tools and decides what to look up: `search_articles`, `get_article` (with an optional date, to read an
  article as it was then), `get_law` and `get_links`. The same tools are offered to any MCP client by
  `server/mcp_server.py` (see below). Local models only for now, and the model must support tool calling in Ollama
  (e.g. qwen2.5, llama3.1).

`rag` needs the search database, `search.db` (about 1 GB, not in git). Once the
[release](https://github.com/nsr223/lkg2/releases/tag/search-db-2026-11-15) is published (not yet), the ready-made one
can be downloaded (about 700 MB):

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

**Search by meaning (optional).** Word search misses an article when the question uses other words than the law
("me pagan tarde el sueldo" against "retrasos continuados en el abono del salario"). To also search by meaning,
give every article a vector with a local embedding model:

```bash
ollama pull bge-m3
python3 scripts/build_vectors.py search.db             # every article: several hours on a laptop; can be stopped and resumed
python3 scripts/build_vectors.py search.db --jur es    # or only state-level laws, about 60 % of the articles
```

Long articles are split into pieces of whole paragraphs, each with its own vector (about 990,000 vectors for
537,000 articles), so a paragraph deep inside a long article can be found by its own meaning. `--max-pieces 1`
embeds only the beginning of each article and takes about half the time.

This writes `search.vec`, `search.vec.ids` and `search.vec.json` next to the database (not in git). When they are
there the server uses them: each question is turned into a vector with the same model (`rag.semantic` in
`config.yaml`) and the articles closest in meaning are added to the ones found by words. `GET /api/qa` then reports
`"search_by_meaning": true`. Everything runs on this computer.

In a conversation, the first question is also searched alone and keeps half of the places among the sources, so
a later message can add sources but cannot push out what the original question found.

**Scenario 3, `rag-graph` (first draft).** The same search as `rag`, then links are followed from the three best
articles found, and the article reached that best matches the question takes the last place among the sources
(`rag.graph.slots`). Two kinds of link (`rag.graph.links`):

- `text`: the articles that the text of a result refers to ("de acuerdo con el artículo 45.1.d)", "el artículo
  139 de la Ley 36/2011"). This is the default.
- `official`: the laws officially linked to a result's law in the graph (developed by, issued under, cited by;
  amendments and repeals are left out), searched with the same question.

An article reached by a link is used only if it shares at least two search terms with the question. The response
says how each one was reached (`via`), and the model sees it too. The number of sources is the same as in `rag`,
so the two scenarios can be compared.

**Scenario 3b, `graph-tools`, and the MCP server.** `server/tools.py` turns LKG 2.0 into four tools:

| Tool | What it returns |
| --- | --- |
| `search_articles(query, law?)` | the articles in force that best match (the scenario 2 search), with an extract |
| `get_article(law, article, date?, apartado?)` | the article's text, today or as it read on `date`, and the list of its versions with the laws that changed it (from `data/hist`) |
| `get_law(law)` | the law's record: rank, scope, in force or repealed, amended or repealed by, years of history |
| `get_links(law, article)` | the articles its text refers to, and the laws officially linked to its law |

Laws can be named as people name them ("Código Civil", "ET", "Ley 29/1994") or by BOE id; articles as "1964",
"9 bis", "disposición adicional vigésima octava" or "anexo II".

In scenario 3b (`server/agent.py`) a local model gets these tools through Ollama's tool calling. It may call them
for up to six rounds and is then asked to answer with what it has. The response lists every call (`tool_calls`)
and every article the tools showed (`sources`, with `via` saying which tool showed it). The prompt matches the RAG
prompt except for how to use the tools.

```bash
python server/ask_all.py --questions ../legal-qa-benchmark/data/noah/questions1.jsonl \
    --scenario graph-tools --out ../legal-qa-benchmark/results/lkg2_graph_tools.jsonl
```

The same tools as an MCP server, for Claude Desktop, an IDE or another agent (runs on this computer, over stdio):

```bash
pip install "mcp>=1.2"
python server/mcp_server.py      # normally started by the client; example client entry:
# "mcpServers": {"lkg2": {"command": "python3", "args": ["/path/to/lkg2/server/mcp_server.py"]}}
```

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
