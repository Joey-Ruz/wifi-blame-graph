"""Home page (graphs/index.html): the time-of-day pattern across all days, then
every daily graph with its key numbers, the weekly graph and the report.

Drawn by the graphs step (nightly and on demand).
"""

import html
from datetime import datetime
from pathlib import Path
from statistics import median

import plotly.graph_objects as go

from wifiblame import analysis, config, logfile, outages

DOWNLOAD = "#2a78d6"
UPLOAD = "#eb6834"
INK_SECONDARY = "#52514e"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"


def hour_pattern(rows):
    """Median download and upload for each hour of the day, across all days,
    from successful reliable tests. Returns [(hour, down, up, tests)]."""
    by_hour = {}
    for row in rows:
        if row["status"] != logfile.STATUS_OK or analysis.is_unreliable(row):
            continue
        if row.get("download_mbps") is None:
            continue
        by_hour.setdefault(analysis.row_time(row).hour, []).append(row)
    pattern = []
    for hour in sorted(by_hour):
        hour_rows = by_hour[hour]
        downs = [r["download_mbps"] for r in hour_rows]
        ups = [r["upload_mbps"] for r in hour_rows if r.get("upload_mbps") is not None]
        pattern.append((hour, median(downs), median(ups) if ups else None, len(hour_rows)))
    return pattern


def day_summaries(rows, all_outages):
    """Newest first: date, test count, typical speeds, anomalies, outages."""
    summaries = []
    for day_rows in analysis.group_by_day(rows).values():
        day = analysis.row_day(day_rows[0])
        tested = [r for r in day_rows if r["status"] in (logfile.STATUS_OK, logfile.STATUS_FAILED)]
        day_outages = outages.outages_between(all_outages, day, day)
        outage_minutes = 0
        for outage in day_outages:
            outage_minutes += outage["minutes"]
        summaries.append({
            "day": day,
            "tests": len(tested),
            "download": analysis.typical_speed(tested, "download_mbps"),
            "upload": analysis.typical_speed(tested, "upload_mbps"),
            "anomalies": len(analysis.find_anomalies(day_rows, rows)),
            "outages": len(day_outages),
            "outage_minutes": outage_minutes,
        })
    summaries.sort(key=lambda item: item["day"], reverse=True)
    return summaries


def _hour_label(hour):
    return datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")


def pattern_chart(pattern, settings):
    if not pattern:
        return "<p>No tests yet.</p>"
    labels = [_hour_label(hour) for hour, _d, _u, _n in pattern]
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=labels, y=[d for _h, d, _u, _n in pattern], name="Download", mode="lines+markers",
        line={"color": DOWNLOAD, "width": 2}, marker={"size": 7},
        customdata=[n for _h, _d, _u, n in pattern],
        hovertemplate="%{x}: %{y:.0f} Mbps download (%{customdata} tests)<extra></extra>"))
    figure.add_trace(go.Scatter(
        x=labels, y=[u for _h, _d, u, _n in pattern], name="Upload", mode="lines+markers",
        line={"color": UPLOAD, "width": 2}, marker={"size": 7},
        hovertemplate="%{x}: %{y:.0f} Mbps upload<extra></extra>"))
    plan = settings["plan_download_mbps"]
    if plan:
        figure.add_hline(y=plan, line={"color": INK_SECONDARY, "width": 1.2, "dash": "dash"},
                         annotation_text=f"Plan download {plan:g} Mbps",
                         annotation_position="top left")
    figure.update_layout(
        height=360, margin={"l": 50, "r": 20, "t": 20, "b": 40},
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, hovermode="x unified",
        font={"family": "system-ui, -apple-system, Segoe UI, sans-serif", "color": INK_SECONDARY},
        legend={"orientation": "h", "y": 1.12},
        yaxis={"title": "Typical speed (Mbps)", "rangemode": "tozero", "gridcolor": GRID},
        xaxis={"gridcolor": GRID},
    )
    return figure.to_html(full_html=False, include_plotlyjs=False,
                          config={"displaylogo": False, "responsive": True})


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wifi-blame-graph</title>
<style>
  :root {{ --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --line: #e1e0d9; --link: #2a78d6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --line: #2c2c2a; --link: #3987e5; }}
  }}
  body {{ margin: 0 auto; max-width: 1000px; padding: 24px 16px; background: var(--surface);
         color: var(--ink); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 28px 0 8px; }}
  .sub {{ color: var(--ink-2); margin: 0 0 16px; }}
  .links a {{ margin-right: 16px; }}
  a {{ color: var(--link); }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
  th {{ color: var(--ink-2); font-weight: 600; }}
  summary {{ cursor: pointer; color: var(--ink-2); }}
</style>
</head>
<body>
<script src="plotly.min.js"></script>
<h1>wifi-blame-graph</h1>
<p class="sub">{subtitle}</p>
<p class="links">{links}</p>
<h2>Speed by time of day</h2>
<p class="sub">Typical speed for each hour, across every day tested. Steady dips at the same
hours (often evenings) usually point to congestion at your provider.</p>
{chart}
<details><summary>Show as a table</summary><div class="table-wrap">{pattern_table}</div></details>
<h2>Days</h2>
<div class="table-wrap">{days_table}</div>
<script>
  (function () {{
    var plot = document.querySelector(".plotly-graph-div");
    if (!plot || !window.matchMedia || !window.matchMedia("(prefers-color-scheme: dark)").matches) return;
    Plotly.relayout(plot, {{ paper_bgcolor: "#1a1a19", plot_bgcolor: "#1a1a19",
      "font.color": "#c3c2b7", "xaxis.gridcolor": "#2c2c2a", "yaxis.gridcolor": "#2c2c2a" }});
  }})();
</script>
</body>
</html>
"""


def _mbps(value):
    return "—" if value is None else f"{value:.0f}"


def render(rows, all_outages, graphs_dir, settings):
    graphs_dir = Path(graphs_dir)
    pattern = hour_pattern(rows)
    summaries = day_summaries(rows, all_outages)

    links = []
    if (graphs_dir / "week" / "last-7-days.html").exists():
        links.append('<a href="week/last-7-days.html">Last 7 days</a>')
    if (graphs_dir / "report" / "last-7-days.html").exists():
        links.append('<a href="report/last-7-days.html">Provider report (last 7 days)</a>')

    pattern_rows = ["<table><tr><th>Hour</th><th>Download</th><th>Upload</th><th>Tests</th></tr>"]
    for hour, down, up, count in pattern:
        pattern_rows.append(f"<tr><td>{_hour_label(hour)}</td><td>{_mbps(down)} Mbps</td>"
                            f"<td>{_mbps(up)} Mbps</td><td>{count}</td></tr>")
    pattern_rows.append("</table>")

    day_rows = ["<table><tr><th>Day</th><th>Tests</th><th>Typical down</th><th>Typical up</th>"
                "<th>Anomalies</th><th>Outages</th><th>Graph</th></tr>"]
    for item in summaries:
        day = item["day"]
        name = day.isoformat()
        graph_links = []
        if (graphs_dir / "daily" / f"{name}.html").exists():
            graph_links.append(f'<a href="daily/{name}.html">interactive</a>')
        if (graphs_dir / "daily" / f"{name}.png").exists():
            graph_links.append(f'<a href="daily/{name}.png">image</a>')
        outage_text = "—"
        if item["outages"]:
            outage_text = f"{item['outages']} ({item['outage_minutes']} min)"
        day_rows.append(
            f"<tr><td>{day.strftime('%a')} {day.strftime('%m/%d/%Y')}</td><td>{item['tests']}</td>"
            f"<td>{_mbps(item['download'])} Mbps</td><td>{_mbps(item['upload'])} Mbps</td>"
            f"<td>{item['anomalies']}</td><td>{outage_text}</td>"
            f"<td>{' · '.join(graph_links) or '—'}</td></tr>")
    day_rows.append("</table>")

    tested_days = len(summaries)
    subtitle = f"{tested_days} day{'s' if tested_days != 1 else ''} of results"
    if settings["plan_download_mbps"]:
        subtitle += f" · plan {settings['plan_download_mbps']:g} Mbps download"
    return PAGE.format(subtitle=html.escape(subtitle), links=" ".join(links) or "",
                       chart=pattern_chart(pattern, settings),
                       pattern_table="".join(pattern_rows), days_table="".join(day_rows))


def write_home(rows, all_outages, graphs_dir, settings=None):
    if settings is None:
        settings = config.current()
    path = Path(graphs_dir) / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows, all_outages, graphs_dir, settings))
    return path
