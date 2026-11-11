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
- **Article-by-article history** (so far for 8,590 of the 12,443 consolidated laws: 8,060 national and 530 regional, about 28,900 versions; the rest are being added): read a law as it stood on any date, see every version of an article with the reform that introduced it, and the words each reform changed.
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
