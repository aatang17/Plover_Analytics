"""Assemble index.html: tokens (copied verbatim from the observatory), styles, data snapshot, app."""
import pathlib
here = pathlib.Path(__file__).parent
tokens = (here / "../../../observatory/web/assets/tokens.css").read_text()
css = (here / "styles.css").read_text()
data = (here / "data.json").read_text().replace("</", "<\\/")
js = (here / "app.js").read_text()
html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Investment Assistant</title>
<meta name="description" content="Plover Analytics Investment Assistant prototype: a desk of specialist agents over the Observatory MCP tools.">
<meta name="robots" content="noindex">
<style id="tokens">
{tokens}
</style>
<style>
{css}
</style>
</head>
<body>
<script id="data" type="application/json">{data}</script>
<script>
{js}
</script>
</body>
</html>
"""
(here / "index.html").write_text(html)
print("index.html", len(html) // 1024, "KB")
