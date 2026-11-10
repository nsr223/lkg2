"""Read EuroVoc (SKOS core RDF/XML) and keep what the mapping needs.
Usage: python3 -I eurovoc_parse.py <eurovoc_in_skos_core_concepts.rdf> <out.json>"""
import json, sys, xml.etree.ElementTree as ET
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"; SKOS = "{http://www.w3.org/2004/02/skos/core#}"
XML = "{http://www.w3.org/XML/1998/namespace}lang"
src, out = sys.argv[1], sys.argv[2]
concepts, tags = {}, {}
for ev, e in ET.iterparse(src, events=("end",)):
    if e.tag != RDF + "Description":
        continue
    uri = e.get(RDF + "about") or ""
    c = {"es": None, "en": None, "alt_es": [], "broader": [], "schemes": [], "types": []}
    for ch in e:
        t = ch.tag.split("}")[-1]; tags[t] = tags.get(t, 0) + 1
        lang = ch.get(XML)
        if ch.tag == SKOS + "prefLabel" and lang in ("es", "en"): c[lang] = ch.text
        elif ch.tag == SKOS + "altLabel" and lang == "es": c["alt_es"].append(ch.text)
        elif ch.tag == SKOS + "broader": c["broader"].append(ch.get(RDF + "resource").rsplit("/", 1)[-1])
        elif ch.tag == SKOS + "inScheme": c["schemes"].append(ch.get(RDF + "resource").rsplit("/", 1)[-1])
        elif ch.tag == RDF + "type": c["types"].append(ch.get(RDF + "resource").rsplit("#", 1)[-1].rsplit("/", 1)[-1])
        elif t == "deprecated": c["deprecated"] = (ch.text or "").strip()
    if uri.startswith("http://eurovoc.europa.eu/"):
        concepts[uri.rsplit("/", 1)[-1]] = c
    e.clear()
json.dump(concepts, open(out, "w"), ensure_ascii=False)
print(len(concepts), "resources; tags:", sorted(tags.items(), key=lambda x: -x[1])[:15])
from collections import Counter
print("types:", Counter(t for c in concepts.values() for t in c["types"]).most_common(8))
print("with es label:", sum(1 for c in concepts.values() if c["es"]))
