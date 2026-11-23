"""LKG 2.0 as an MCP server: Spanish legislation, its article history and its links, for any MCP client.

Runs on this computer over stdio; nothing leaves the machine except what the client itself sends elsewhere.

    pip install -r server/requirements.txt "mcp>=1.2"
    python server/mcp_server.py                 # normally started by the MCP client, not by hand

Example client entry (Claude Desktop's claude_desktop_config.json; use your own paths):

    "mcpServers": {"lkg2": {"command": "python3", "args": ["/path/to/lkg2/server/mcp_server.py"]}}

It needs search.db (see the README); search by meaning is used if the vectors exist and Ollama is running,
otherwise the search uses words only. The tools are the same functions the Q&A service uses in scenario 3b
(server/tools.py).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm  # noqa: E402
import retrieve  # noqa: E402
import tools as T  # noqa: E402

try:   # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:   # 1.x
    from mcp.server.fastmcp import FastMCP as Server

ROOT = pathlib.Path(__file__).resolve().parent.parent
_tools = None


def tools():
    """Open the index on first use, so the client's handshake is fast."""
    global _tools
    if _tools is None:
        import yaml
        cfg_path = ROOT / "config.yaml" if (ROOT / "config.yaml").exists() else ROOT / "config.example.yaml"
        config = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        rag = config.get("rag", {})
        db = ROOT / rag.get("database", "search.db")
        if not db.exists():
            raise RuntimeError(f"{db} not found: build it with scripts/build_search.py (see the README).")
        index = retrieve.Index(db, ROOT / "data" / "laws.json", ROOT / "data" / "refs.json")
        sem = rag.get("semantic") or {}
        embed = None
        if index.vectors is not None and sem.get("enabled", True):
            def embed(text):
                try:
                    return llm.embed(sem, text)
                except llm.ModelError:   # Ollama not running: fall back to words only
                    return None
        _tools = T.Tools(index, ROOT, embed=embed)
    return _tools


mcp = Server("lkg2", instructions=(
    "Spanish legislation from LKG 2.0 (Legal Knowledge Graph 2.0, OEG-UPM), built from the consolidated texts of the BOE: "
    "search articles, read an article as it is today or as it read on a past date, see a law's status and amendments, "
    "and follow the references in an article. Texts and answers are in Spanish."))


@mcp.tool()
def search_articles(query: str, law: str | None = None) -> str:
    """Search current Spanish legislation for the articles that answer a question (words and meaning).
    query: the question or legal terms, in Spanish. law: optional, limit to one law by name or number ("Ley 29/1994")."""
    return tools().search_articles(query, law)[0]


@mcp.tool()
def get_article(law: str, article: str, date: str | None = None, apartado: str | None = None) -> str:
    """The text of an article with the list of its versions (dates and amending laws).
    law: name, abbreviation or number ("Código Civil", "ET", "Ley 39/2015"). article: "1964", "9 bis",
    "disposición adicional vigésima octava", "anexo II". date: optional YYYY-MM-DD, to get the version in force
    on that date. apartado: optional paragraph number, for long articles."""
    return tools().get_article(law, article, date, apartado)[0]


@mcp.tool()
def get_law(law: str) -> str:
    """A law's record: rank, scope (state or regional), in force or repealed, laws that amended or repealed it,
    and the years its consolidated versions cover."""
    return tools().get_law(law)[0]


@mcp.tool()
def get_links(law: str, article: str) -> str:
    """The articles an article refers to in its text, and the laws officially linked to its law."""
    return tools().get_links(law, article)[0]


if __name__ == "__main__":
    mcp.run()
