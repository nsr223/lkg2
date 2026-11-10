"""Turn the explorer page made for Claude (artifact) into the standalone page for GitHub Pages.
Usage: python3 -I make_standalone.py <claude page .html> <out index.html>
Changes: adds a normal HTML document head, loads data from data/, removes the AI-summary code
(only works inside Claude), uses normal browser downloads, and links the repository in the footer."""
import re, sys
src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()

def cut(s, start, end_marker, keep_end=True):
    i = s.index(start); j = s.index(end_marker, i)
    return s[:i] + (s[j:] if keep_end else s[j + len(end_marker):])

# 1. a proper document
s = s.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
s = s.replace('<meta charset="utf-8">\n', '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n', 1)
s = s.replace("</style>\n", "</style>\n</head>\n<body>\n", 1)
s = s.rstrip() + "\n</body>\n</html>\n"
# 2. AI summaries: remove (they need Claude); keep a stub "cap" that says no capability is available
s = cut(s, "let dbP = null, sampleP = null;", "async function excerptFor(l){")
s = s.replace("async function excerptFor(l){", "const cap = () => Promise.resolve(null);   // no Claude capabilities outside Claude\nasync function excerptFor(l){", 1)
i = s.index("async function makeSummary(l){"); j = s.index("\n}\n", i) + 3
s = s[:i] + s[j:]
# 3. normal downloads
s = s.replace('const getDownloads = () => dlP || (dlP = cap("downloads"));',
  'const getDownloads = () => dlP || (dlP = Promise.resolve({save: async ({filename, data}) => {\n'
  '  const type = filename.endsWith(".ttl") ? "text/turtle" : "application/ld+json";\n'
  '  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([data], {type})); a.download = filename;\n'
  '  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);\n'
  '}}));')
s = s.replace('filename: `${l.id}.${fmt === "json" ? "jsonld.json" : "ttl.txt"}`', 'filename: `${l.id}.${fmt === "json" ? "jsonld" : "ttl"}`')
# 4. data lives in data/
for a, b in [('fetch("refs.json")', 'fetch("data/refs.json")'), ('fetch(`eli/', 'fetch(`data/eli/'), ('fetch(`history/', 'fetch(`data/history/'),
             ('fetch("history/index.json")', 'fetch("data/history/index.json")'), ('fetch("eurovoc.json")', 'fetch("data/eurovoc.json")'),
             ('fetch("laws.json")', 'fetch("data/laws.json")'), ('fetch(`excerpts/', 'fetch(`data/excerpts/')]:
    assert a in s, a
    s = s.replace(a, b)
# 5. footer: link the code
s = s.replace('<span id="built"></span>.</p>', '<span id="built"></span>. <a href="https://github.com/nsr223/lkg2" target="_blank" rel="noopener">GitHub</a>.</p>')
assert "sample" not in re.sub(r"//.*", "", s.split("<script>")[1]).replace("example", ""), "summary code left"
open(out, "w", encoding="utf-8").write(s)
print("wrote", out)
