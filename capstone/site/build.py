"""Build the static case-study page: inline CSS, JS and results.json into one HTML file and
copy the downloadable files next to it.   python site/build.py  ->  site/dist/"""
import csv, html as html_mod, json, shutil, subprocess, sys
from pathlib import Path
import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC, DIST = ROOT / "site" / "src", ROOT / "site" / "dist"
FILES = DIST / "files"

DIST.mkdir(exist_ok=True)
FILES.mkdir(exist_ok=True)
data = json.loads((ROOT / "results" / "results.json").read_text())

# a few real rows for the "what one row looks like" table: the first in-depth call of four channels
rows = list(csv.DictReader(open(ROOT / "data" / "public" / "vetted_calls.csv")))
sample, seen = [], set()
for want in ("buy", "bearish", "buy", "buy"):
    for r in rows:
        if r["direction"] == want and r["channel"] not in seen and r["in_depth"] == "True" and r["video_date"] >= "2025-01-01":
            sample.append(r); seen.add(r["channel"]); break
data["sample_rows"] = sample

page = (SRC / "index.template.html").read_text()
page = page.replace("/*__CSS__*/", (SRC / "styles.css").read_text())
page = page.replace("/*__JS__*/", (SRC / "app.js").read_text())
page = page.replace("/*__DATA__*/", json.dumps(data, separators=(",", ":")))
(DIST / "index.html").write_text(page)

shutil.copytree(SRC / "fonts", DIST / "fonts", dirs_exist_ok=True)
shutil.copytree(SRC / "img", DIST / "img", dirs_exist_ok=True)
shutil.copy(ROOT / "data" / "public" / "vetted_calls.csv", FILES / "vetted_calls.csv")

SUB = """<!doctype html><html lang="en"><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="noindex,nofollow"><title>{title} · Vetted case study</title>
<style>@font-face{{font-family:Plex;font-weight:100 700;src:url({up}fonts/plex.woff2) format("woff2")}}
body{{background:#FFFFFF;color:#1b1b1b;font:17px/1.6 Plex,system-ui,sans-serif;max-width:760px;margin:32px auto 80px;padding:0 20px}}
h1,h2,h3{{font-weight:600;line-height:1.2}}h1{{font-size:30px}}h2{{font-size:23px;margin-top:2em}}
code,pre{{font-family:ui-monospace,Menlo,monospace;font-size:13.5px;background:#f1f1ec}}code{{padding:1px 4px}}
pre{{padding:14px;overflow-x:auto;line-height:1.45}}pre code{{padding:0}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;font-size:15px}}td,th{{border-bottom:1px solid #deded8;padding:7px 12px 7px 0;text-align:left;vertical-align:top}}
a{{color:inherit}}li{{margin-bottom:6px}}</style>
<p><a href="{up}">&larr; Back to the case study</a></p>{body}</html>"""


def md_page(src, out, title, up="../"):
    body = markdown.markdown(src.read_text(), extensions=["tables", "fenced_code"])
    body = body.replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")
    out.write_text(SUB.format(title=title, body=body, up=up))


md_page(ROOT / "JOURNAL.md", FILES / "JOURNAL.html", "Data journal")
md_page(ROOT / "data" / "public" / "README.md", FILES / "DATASET.html", "Dataset description")

# analysis code: plain copies plus one readable listing page
CODE = FILES / "code"
CODE.mkdir(exist_ok=True)
listing = [("src/common.py", "Shared settings: which labels count as buy or bearish, ticker clean-up"),
           ("src/01_extract.py", "Reads the mentions out of the private database"),
           ("src/02_prices.py", "Downloads prices (Yahoo, Tiingo for delisted companies)"),
           ("src/03_clean.py", "The cleaning steps and the public dataset"),
           ("src/04_analyze.py", "All statistics; writes results.json"),
           ("src/audit_sample.py", "Draws the random sample for the manual check"),
           ("audit/extraction_audit.csv", "Labels from the manual check of 140 rows (no transcript text)"),
           ("results/results.json", "Every number shown on the page"),
           ("site/build.py", "Builds this site"),
           ("notebook/build_notebook.py", "Generates the notebook")]
items = []
for rel, what in listing:
    p = ROOT / rel
    name = rel.replace("/", "__")
    shutil.copy(p, CODE / name)
    items.append(f'<li><a href="{name}">{rel}</a><br>{what}</li>')
(CODE / "index.html").write_text(SUB.format(title="Analysis code", up="../../", body=(
    "<h1>Analysis code</h1><p>The scripts behind the case study, in the order they run. Steps 1 and 2 need the "
    "private Vetted database; everything after the public dataset can be rerun with the notebook.</p><ul>"
    + "".join(items) + "</ul><p>The pipeline that collects the videos is a separate project: "
    '<a href="https://github.com/Mitjaw/vetted-public">github.com/Mitjaw/vetted-public</a>.</p>')))

nb = ROOT / "notebook" / "vetted_capstone.ipynb"
if nb.exists():
    shutil.copy(nb, FILES / nb.name)
    subprocess.run([sys.executable, "-m", "nbconvert", "--to", "html", "--output-dir", str(FILES), str(nb)],
                   check=True, capture_output=True)
print("built", DIST / "index.html", f"{len(page) / 1024:.0f} kB")
