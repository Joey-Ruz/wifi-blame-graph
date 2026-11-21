"""Daily and 7-day graphs, each as a PNG image and an interactive web page.

Run with: .venv/bin/python -m wifiblame.graphs            (last 7 days, weekly, report, home page)
          .venv/bin/python -m wifiblame.graphs 2026-11-20 (one day + weekly)

Three panels share one timeline: speed, Wi-Fi signal, delay.
"""

import html
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import plotly.offline  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from wifiblame import analysis, config, homepage, logfile, outages, report  # noqa: E402

# Colors (validated categorical slots and fixed status colors).
DOWNLOAD = "#2a78d6"
UPLOAD = "#eb6834"
SIGNAL = "#008300"
ROUTER_DELAY = "#1baf7a"
INTERNET_DELAY = "#6250d6"
LOADED_DELAY = "#e87ba4"
CRITICAL = "#d03b3b"
SERIOUS = "#ec835a"
UNRELIABLE = "#898781"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# A longer gap than this between tests breaks the line (laptop asleep or away).
GAP_MINUTES = 25

DAILY_DIR_NAME = "daily"
WEEK_DIR_NAME = "week"
PLOTLY_JS_NAME = "plotly.min.js"


def format_date(day):
    return day.strftime("%m/%d/%Y")


def format_time(moment):
    return moment.strftime("%I:%M %p").lstrip("0")


# ---------- Turning rows into plottable series ----------

def series_with_gaps(rows, value_of):
    """x/y lists for successful tests, with None inserted across long gaps."""
    xs = []
    ys = []
    previous = None
    for row in rows:
        if row["status"] != logfile.STATUS_OK:
            continue
        value = value_of(row)
        if value is None:
            continue
        moment = analysis.row_time(row)
        if previous is not None and moment - previous > timedelta(minutes=GAP_MINUTES):
            xs.append(previous + timedelta(minutes=1))
            ys.append(None)
        xs.append(moment)
        ys.append(value)
        previous = moment
    return xs, ys


def field(name):
    def value_of(row):
        return row.get(name)
    return value_of


def _fmt(value, unit, digits=1):
    if value is None:
        return "—"
    return f"{value:.{digits}f} {unit}"


def _plan_share(value, setting):
    plan = config.current()[setting]
    if value is None or not plan:
        return ""
    return f" ({value / plan * 100:.0f}% of plan)"


def outage_label(outage):
    start = outage["start"]
    return (f"{outage['kind']} outage {format_date(start.date())} {format_time(start)}, "
            f"{outage['minutes']} min")


def describe(row, anomaly_reasons):
    """Every logged detail for one test, as hover lines."""
    moment = analysis.row_time(row)
    lines = [f"<b>{format_date(moment.date())} {format_time(moment)}</b>"]
    if row["status"] != logfile.STATUS_OK:
        lines.append(f"Status: {row['status']} {row.get('error') or ''}".strip())
    else:
        lines.append(f"Download: {_fmt(row['download_mbps'], 'Mbps')}"
                     f"{_plan_share(row['download_mbps'], 'plan_download_mbps')}")
        lines.append(f"Upload: {_fmt(row['upload_mbps'], 'Mbps')}"
                     f"{_plan_share(row['upload_mbps'], 'plan_upload_mbps')}")
        lines.append(
            f"Delay: {_fmt(row['ping_ms'], 'ms')} · jitter {_fmt(row['jitter_ms'], 'ms')}"
            f" · loss {_fmt(row['packet_loss_pct'], '%')}"
        )
        lines.append(
            f"Delay when busy: down {_fmt(row['download_loaded_ms'], 'ms')}"
            f" · up {_fmt(row['upload_loaded_ms'], 'ms')}"
        )
    lines.append(f"Delay to router: {_fmt(row.get('router_ping_ms'), 'ms')}")
    lines.append(
        f"Signal: {_fmt(row.get('signal_dbm'), 'dBm', 0)} · noise {_fmt(row.get('noise_dbm'), 'dBm', 0)}"
        f" · {row.get('band') or '—'} ch {row.get('channel') or '—'}"
    )
    if row["status"] == logfile.STATUS_OK:
        tool = "Apple's built-in tester" if row.get("tool") == "apple" else "Ookla Speedtest"
        lines.append(f"Tested with: {tool}")
        lines.append(f"Server: {row.get('server_name') or '—'} ({row.get('server_location') or '—'})")
        lines.append(f"Provider: {row.get('isp') or '—'} · IP {row.get('public_ip') or '—'}")
    if row.get("data_used_mb") is not None:
        lines.append(f"Data used by this test: {row['data_used_mb']:.0f} MB")
    lines.append(
        f"Background download: {_fmt(row.get('background_mbps'), 'Mbps')}"
        f" · VPN {'on' if row.get('vpn_on') else 'off'}"
        f" · {'plugged in' if row.get('on_power') else 'on battery' if row.get('on_power') is False else 'power unknown'}"
    )
    reasons = analysis.unreliable_reasons(row)
    if reasons:
        lines.append("Unreliable: " + ", ".join(reasons))
    for _kind, text in anomaly_reasons:
        lines.append(f"<b>Anomaly: {html.escape(text)}</b>")
    if row.get("result_url"):
        lines.append("Click to open the speedtest.net result")
    return "<br>".join(lines)


class GraphData:
    """Everything both graph formats need, worked out once."""

    def __init__(self, rows, data_line="", history=None, outage_list=None):
        self.rows = rows
        self.data_line = data_line
        self.outages = outage_list or []
        self.plan_download = config.current()["plan_download_mbps"]
        self.plan_upload = config.current()["plan_upload_mbps"]
        self.anomalies = analysis.find_anomalies(rows, history)
        self.download = series_with_gaps(rows, field("download_mbps"))
        self.upload = series_with_gaps(rows, field("upload_mbps"))
        self.signal = series_with_gaps(rows, field("signal_dbm"))
        self.router_delay = series_with_gaps(rows, field("router_ping_ms"))
        self.internet_delay = series_with_gaps(rows, field("ping_ms"))
        self.loaded_delay = series_with_gaps(rows, analysis.loaded_delay_ms)

        self.slow = {"download": ([], []), "upload": ([], [])}
        self.failed_times = []
        for index, kinds in self.anomalies.items():
            row = rows[index]
            for kind, _text in kinds:
                if kind == "failed":
                    self.failed_times.append(analysis.row_time(row))
                else:
                    xs, ys = self.slow[kind]
                    xs.append(analysis.row_time(row))
                    ys.append(row[f"{kind}_mbps"])

        self.apple_x = []
        self.apple_y = []
        for row in rows:
            if row["status"] == logfile.STATUS_OK and row.get("tool") == "apple":
                for measure in ("download_mbps", "upload_mbps"):
                    if row.get(measure) is not None:
                        self.apple_x.append(analysis.row_time(row))
                        self.apple_y.append(row[measure])

        self.unreliable_x = []
        self.unreliable_down = []
        self.unreliable_up = []
        for row in rows:
            if row["status"] == logfile.STATUS_OK and analysis.is_unreliable(row):
                self.unreliable_x.append(analysis.row_time(row))
                self.unreliable_down.append(row.get("download_mbps"))
                self.unreliable_up.append(row.get("upload_mbps"))

    def tested_rows(self):
        tested = []
        for row in self.rows:
            if row["status"] in (logfile.STATUS_OK, logfile.STATUS_FAILED):
                tested.append(row)
        return tested

    def summary(self):
        tested = self.tested_rows()
        down = analysis.typical_speed(tested, "download_mbps")
        up = analysis.typical_speed(tested, "upload_mbps")
        parts = [f"{len(tested)} tests"]
        if down is not None:
            parts.append(f"typical {down:.0f} down / {up:.0f} up Mbps")
        count = len(self.anomalies)
        parts.append(f"{count} anomaly" if count == 1 else f"{count} anomalies")
        if self.outages:
            minutes = 0
            for outage in self.outages:
                minutes += outage["minutes"]
            label = "outage" if len(self.outages) == 1 else "outages"
            parts.append(f"{len(self.outages)} {label} ({minutes} min)")
        if self.data_line:
            parts.append(self.data_line)
        return " · ".join(parts)


# ---------- PNG ----------

def _style_axis(axis, ylabel):
    axis.set_facecolor(SURFACE)
    axis.grid(True, color=GRID, linewidth=0.6)
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(AXIS)
    axis.tick_params(colors=INK_SECONDARY, labelsize=9)
    axis.set_ylabel(ylabel, color=INK_SECONDARY, fontsize=10)


def _line(axis, series, color, label, marker, is_week):
    xs, ys = series
    plotted_y = []
    for value in ys:
        plotted_y.append(float("nan") if value is None else value)
    # A week holds ~700 tests; dots would bury the line, so the week shows lines only.
    marker_size = 0 if is_week else 4
    line_width = 1.5 if is_week else 2
    axis.plot(xs, plotted_y, color=color, linewidth=line_width, marker=marker,
              markersize=marker_size, label=label)


def _legend_above(axis, columns):
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.01), fontsize=9,
                frameon=False, ncol=columns, borderaxespad=0)


def _outage_bands_png(axes, outage_list):
    labeled = set()
    for outage in outage_list:
        color = CRITICAL if outage["kind"] == outages.KIND_PROVIDER else SERIOUS
        for index, axis in enumerate(axes):
            label = None
            if index == 0 and outage["kind"] not in labeled:
                label = f"{outage['kind']} outage"
                labeled.add(outage["kind"])
            axis.axvspan(outage["start"], outage["end"], color=color, alpha=0.18,
                         linewidth=0, label=label, zorder=1)


def write_png(data, path, title, x_range, is_week):
    figure, (speed, signal, delay) = plt.subplots(
        3, 1, sharex=True, figsize=(13, 9.5),
        gridspec_kw={"height_ratios": [3, 1.4, 2]},
    )
    figure.patch.set_facecolor(SURFACE)

    _style_axis(speed, "Speed (Mbps)")
    _line(speed, data.download, DOWNLOAD, "Download", "o", is_week)
    _line(speed, data.upload, UPLOAD, "Upload", "o", is_week)
    if data.unreliable_x:
        speed.scatter(data.unreliable_x + data.unreliable_x,
                      data.unreliable_down + data.unreliable_up,
                      s=70, facecolors="none", edgecolors=UNRELIABLE, linewidths=1.5,
                      label="Unreliable (VPN, Low Power Mode, or busy Mac)", zorder=4)
    if data.apple_x:
        speed.scatter(data.apple_x, data.apple_y, marker="D", s=60, facecolors="none",
                      edgecolors=INK_SECONDARY, linewidths=1.3,
                      label="Tested with Apple's tool", zorder=4)
    slow_x = data.slow["download"][0] + data.slow["upload"][0]
    slow_y = data.slow["download"][1] + data.slow["upload"][1]
    if slow_x:
        speed.scatter(slow_x, slow_y, s=160, facecolors="none", edgecolors=CRITICAL,
                      linewidths=2, label="Anomaly: under half of typical", zorder=5)
    if data.failed_times:
        speed.scatter(data.failed_times, [0] * len(data.failed_times), marker="X",
                      s=90, color=CRITICAL, label="Anomaly: test failed", zorder=5,
                      clip_on=False)
    plan_top = 0
    for plan, label in ((data.plan_download, "Plan download"), (data.plan_upload, "Plan upload")):
        if plan:
            speed.axhline(plan, color=INK_SECONDARY, linewidth=1.2, linestyle=(0, (4, 3)),
                          label=f"{label} {plan:g} Mbps", zorder=2)
            plan_top = max(plan_top, plan)
    _outage_bands_png((speed, signal, delay), data.outages)
    speed.set_ylim(bottom=0)
    if plan_top:
        speed.set_ylim(top=max(speed.get_ylim()[1], plan_top * 1.08))
    _legend_above(speed, 4)

    _style_axis(signal, "Wi-Fi signal (dBm)\nhigher is better")
    _line(signal, data.signal, SIGNAL, "Signal strength", "o", is_week)

    _style_axis(delay, "Delay (ms)")
    _line(delay, data.router_delay, ROUTER_DELAY, "To your router", "s", is_week)
    _line(delay, data.internet_delay, INTERNET_DELAY, "To the internet", "o", is_week)
    _line(delay, data.loaded_delay, LOADED_DELAY, "Internet, while busy", "^", is_week)
    delay.set_ylim(bottom=0)
    _legend_above(delay, 3)

    delay.set_xlim(x_range)
    if is_week:
        # Day names sit at noon, centered on each day; gridlines mark midnight.
        delay.xaxis.set_major_locator(mdates.HourLocator(byhour=12))
        delay.xaxis.set_major_formatter(mdates.DateFormatter("%a %m/%d"))
        delay.xaxis.set_minor_locator(mdates.DayLocator())
        for axis in (speed, signal, delay):
            axis.grid(False, axis="x", which="major")
            axis.grid(True, axis="x", which="minor", color=GRID, linewidth=0.6)
        delay.tick_params(axis="x", which="major", length=0)
    else:
        delay.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 2)))
        delay.xaxis.set_major_formatter(mdates.DateFormatter("%-I %p"))

    figure.suptitle(title, x=0.06, ha="left", fontsize=15, color=INK, fontweight="bold")
    figure.text(0.06, 0.94, data.summary(), fontsize=10, color=INK_SECONDARY)
    figure.subplots_adjust(top=0.88, hspace=0.2, left=0.08, right=0.98, bottom=0.06)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=120, facecolor=SURFACE)
    plt.close(figure)


# ---------- Interactive web page ----------

def _hover_points(data):
    """x, hover text and result link for every tested row, per measure."""
    points = {"download_mbps": ([], [], []), "upload_mbps": ([], [], [])}
    for index, row in enumerate(data.rows):
        if row["status"] != logfile.STATUS_OK:
            continue
        text = describe(row, data.anomalies.get(index, []))
        for measure in points:
            if row.get(measure) is None:
                continue
            xs, ys, extras = points[measure]
            xs.append(analysis.row_time(row))
            ys.append(row[measure])
            extras.append([text, row.get("result_url") or ""])
    return points


def _web_line(figure, series, color, name, symbol, row_number):
    xs, ys = series
    trace = go.Scatter(
        x=xs, y=ys, name=name, mode="lines+markers",
        line={"color": color, "width": 2},
        marker={"color": color, "size": 6, "symbol": symbol},
        connectgaps=False,
        hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>",
    )
    figure.add_trace(trace, row=row_number, col=1)


def build_web_figure(data, title, x_range, is_week):
    figure = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.5, 0.22, 0.28],
        subplot_titles=("Speed (Mbps)", "Wi-Fi signal (dBm, higher is better)", "Delay (ms)"),
    )

    # Speed lines draw without hover; full-detail markers sit on top of them.
    for series, color, name in ((data.download, DOWNLOAD, "Download"),
                                (data.upload, UPLOAD, "Upload")):
        xs, ys = series
        figure.add_trace(go.Scatter(
            x=xs, y=ys, name=name, mode="lines", line={"color": color, "width": 2},
            connectgaps=False, hoverinfo="skip", legendgroup=name,
        ), row=1, col=1)
    points = _hover_points(data)
    for measure, color, name in (("download_mbps", DOWNLOAD, "Download"),
                                 ("upload_mbps", UPLOAD, "Upload")):
        xs, ys, extras = points[measure]
        figure.add_trace(go.Scatter(
            x=xs, y=ys, name=name, mode="markers", showlegend=False, legendgroup=name,
            marker={"color": color, "size": 8, "line": {"color": SURFACE, "width": 2}},
            customdata=extras, hovertemplate="%{customdata[0]}<extra></extra>",
        ), row=1, col=1)

    if data.unreliable_x:
        figure.add_trace(go.Scatter(
            x=data.unreliable_x + data.unreliable_x,
            y=data.unreliable_down + data.unreliable_up,
            name="Unreliable (VPN, Low Power Mode, or busy Mac)", mode="markers",
            marker={"symbol": "circle-open", "size": 14, "color": UNRELIABLE,
                    "line": {"width": 2}},
            hoverinfo="skip",
        ), row=1, col=1)
    if data.apple_x:
        figure.add_trace(go.Scatter(
            x=data.apple_x, y=data.apple_y, name="Tested with Apple's tool", mode="markers",
            marker={"symbol": "diamond-open", "size": 13, "color": INK_SECONDARY,
                    "line": {"width": 1.5}},
            hoverinfo="skip",
        ), row=1, col=1)
    slow_x = data.slow["download"][0] + data.slow["upload"][0]
    slow_y = data.slow["download"][1] + data.slow["upload"][1]
    if slow_x:
        figure.add_trace(go.Scatter(
            x=slow_x, y=slow_y, name="Anomaly: under half of typical", mode="markers",
            marker={"symbol": "circle-open", "size": 20, "color": CRITICAL,
                    "line": {"width": 3}},
            hoverinfo="skip",
        ), row=1, col=1)
    if data.failed_times:
        failed_text = []
        for index in data.anomalies:
            row = data.rows[index]
            if row["status"] == logfile.STATUS_FAILED:
                failed_text.append([describe(row, data.anomalies[index]), ""])
        figure.add_trace(go.Scatter(
            x=data.failed_times, y=[0] * len(data.failed_times),
            name="Anomaly: test failed", mode="markers",
            marker={"symbol": "x", "size": 12, "color": CRITICAL},
            customdata=failed_text, hovertemplate="%{customdata[0]}<extra></extra>",
            cliponaxis=False,
        ), row=1, col=1)

    for plan, label in ((data.plan_download, "Plan download"), (data.plan_upload, "Plan upload")):
        if plan:
            figure.add_hline(y=plan, line={"color": INK_SECONDARY, "width": 1.2, "dash": "dash"},
                             annotation_text=f"{label} {plan:g} Mbps",
                             annotation_position="top left", row=1, col=1)
    shown_kinds = set()
    for outage in data.outages:
        color = CRITICAL if outage["kind"] == outages.KIND_PROVIDER else SERIOUS
        figure.add_vrect(x0=outage["start"], x1=outage["end"], fillcolor=color, opacity=0.18,
                         line_width=0, layer="below", row="all", col=1)
        if outage["kind"] not in shown_kinds:
            shown_kinds.add(outage["kind"])
            figure.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers", name=f"{outage['kind']} outage",
                marker={"symbol": "square", "size": 12, "color": color, "opacity": 0.4},
            ), row=1, col=1)

    _web_line(figure, data.signal, SIGNAL, "Signal strength", "circle", 2)
    _web_line(figure, data.router_delay, ROUTER_DELAY, "Delay to your router", "square", 3)
    _web_line(figure, data.internet_delay, INTERNET_DELAY, "Delay to the internet", "circle", 3)
    _web_line(figure, data.loaded_delay, LOADED_DELAY, "Internet delay while busy",
              "triangle-up", 3)

    figure.update_layout(
        title={"text": f"{title}<br><sup>{data.summary()}</sup>", "x": 0.02},
        height=900,
        hovermode="closest",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font={"family": "system-ui, -apple-system, Segoe UI, sans-serif",
              "color": INK_SECONDARY},
        legend={"orientation": "h", "y": -0.06},
        margin={"l": 60, "r": 20, "t": 90, "b": 60},
    )
    figure.update_xaxes(gridcolor=GRID, linecolor=AXIS, range=list(x_range),
                        tickformat="%a %m/%d" if is_week else "%-I %p")
    figure.update_yaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False)
    figure.update_yaxes(rangemode="tozero", row=1, col=1)
    figure.update_yaxes(rangemode="tozero", row=3, col=1)
    return figure


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --line: #e1e0d9; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --line: #2c2c2a; }}
  }}
  body {{ margin: 0; padding: 16px; background: var(--surface); color: var(--ink);
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  details {{ margin-top: 24px; }}
  summary {{ cursor: pointer; color: var(--ink-2); }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }}
  th, td {{ border-bottom: 1px solid var(--line); padding: 4px 8px; text-align: left;
            white-space: nowrap; }}
  th {{ color: var(--ink-2); font-weight: 600; }}
  td.flag {{ color: #d03b3b; font-weight: 600; }}
  a {{ color: inherit; }}
</style>
</head>
<body>
<script src="{plotly_js}"></script>
{chart}
{outage_list}
<details>
<summary>Show every test as a table</summary>
<div class="table-wrap">{table}</div>
</details>
<script>
  (function () {{
    var plot = document.querySelector(".plotly-graph-div");
    if (!plot) return;
    plot.on("plotly_click", function (event) {{
      var point = event.points[0];
      if (point.customdata && point.customdata[1]) window.open(point.customdata[1], "_blank");
    }});
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {{
      var dark = {{
        paper_bgcolor: "#1a1a19", plot_bgcolor: "#1a1a19",
        "font.color": "#c3c2b7", "title.font.color": "#f0efec"
      }};
      ["xaxis", "xaxis2", "xaxis3", "yaxis", "yaxis2", "yaxis3"].forEach(function (name) {{
        dark[name + ".gridcolor"] = "#2c2c2a";
        dark[name + ".linecolor"] = "#383835";
      }});
      Plotly.relayout(plot, dark);
      // The ring around each hover dot matches the surface, so it follows to dark.
      var ringed = [];
      plot.data.forEach(function (trace, index) {{
        if (trace.mode === "markers" && trace.showlegend === false) ringed.push(index);
      }});
      if (ringed.length) Plotly.restyle(plot, {{"marker.line.color": "#1a1a19"}}, ringed);
    }}
  }})();
</script>
</body>
</html>
"""


def build_outage_list(data):
    if not data.outages:
        return ""
    parts = ["<h2 style=\"font-size:16px;margin:24px 0 8px\">Outages</h2><ul>"]
    for outage in data.outages:
        parts.append(f"<li>{html.escape(outage_label(outage))}</li>")
    parts.append("</ul>")
    return "".join(parts)


def build_table(data):
    headings = ["Time", "Status", "Down Mbps", "Up Mbps", "Delay ms", "Busy delay ms",
                "Router ms", "Signal dBm", "Band", "Provider", "Notes", "Result"]
    parts = ["<table><thead><tr>"]
    for heading in headings:
        parts.append(f"<th>{heading}</th>")
    parts.append("</tr></thead><tbody>")
    for index, row in enumerate(data.rows):
        moment = analysis.row_time(row)
        notes = []
        for _kind, text in data.anomalies.get(index, []):
            notes.append("Anomaly: " + text)
        notes.extend(analysis.unreliable_reasons(row))
        if row.get("tool") == "apple":
            notes.append("Apple's tester")
        link = ""
        if row.get("result_url"):
            link = f'<a href="{html.escape(row["result_url"])}" target="_blank">open</a>'
        cells = [
            f"{format_date(moment.date())} {format_time(moment)}",
            row["status"],
            _fmt(row.get("download_mbps"), "", 1).strip(),
            _fmt(row.get("upload_mbps"), "", 1).strip(),
            _fmt(row.get("ping_ms"), "", 1).strip(),
            _fmt(analysis.loaded_delay_ms(row), "", 1).strip(),
            _fmt(row.get("router_ping_ms"), "", 1).strip(),
            _fmt(row.get("signal_dbm"), "", 0).strip(),
            row.get("band") or "—",
            row.get("isp") or "—",
        ]
        parts.append("<tr>")
        for cell in cells:
            parts.append(f"<td>{html.escape(str(cell))}</td>")
        flag_class = ' class="flag"' if index in data.anomalies else ""
        parts.append(f"<td{flag_class}>{html.escape('; '.join(notes))}</td>")
        parts.append(f"<td>{link}</td></tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def ensure_plotly_js(graphs_dir):
    path = Path(graphs_dir) / PLOTLY_JS_NAME
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plotly.offline.get_plotlyjs())
    return path


def write_html(data, path, title, x_range, is_week, graphs_dir):
    ensure_plotly_js(graphs_dir)
    figure = build_web_figure(data, title, x_range, is_week)
    chart = figure.to_html(full_html=False, include_plotlyjs=False,
                           config={"displaylogo": False, "responsive": True})
    page = PAGE_TEMPLATE.format(
        title=html.escape(title),
        plotly_js=f"../{PLOTLY_JS_NAME}",
        chart=chart,
        table=build_table(data),
        outage_list=build_outage_list(data),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)


# ---------- Entry points ----------

def rows_between(rows, first_day, last_day):
    selected = []
    for row in rows:
        day = analysis.row_day(row)
        if first_day <= day <= last_day:
            selected.append(row)
    selected.sort(key=analysis.row_time)
    return selected


def data_use_line(all_rows, through_day, day_gb=None):
    """Speed-test data use against the monthly cap, as of the end of through_day."""
    month_gb = analysis.month_data_used_gb(all_rows, through_day)
    month_name = through_day.strftime("%B")
    cap_gb = config.current()["data_cap_gb"]
    if cap_gb is None:
        month_part = f"{month_gb:.0f} GB used by tests in {month_name} so far"
    else:
        cap_text = f"{cap_gb / 1000:g} TB" if cap_gb >= 1000 else f"{cap_gb:g} GB"
        share = month_gb / cap_gb * 100
        month_part = (f"{month_gb:.0f} GB of the {cap_text} cap"
                      f" used by tests in {month_name} so far ({share:.1f}%)")
    if day_gb is None:
        return month_part
    return f"tests used {day_gb:.1f} GB this day · {month_part}"


def make_day_graphs(all_rows, day, graphs_dir, all_outages=None):
    """Returns the paths written, or [] when the day has no logged runs."""
    rows = rows_between(all_rows, day, day)
    if not rows:
        return []
    day_gb = analysis.day_data_used_gb(all_rows, day)
    history = rows_between(all_rows, day - timedelta(days=analysis.BASELINE_DAYS - 1), day)
    day_outages = outages.outages_between(all_outages or [], day, day)
    data = GraphData(rows, data_use_line(all_rows, day, day_gb), history, day_outages)
    start = datetime.combine(day, datetime.min.time()).replace(hour=config.current()["first_hour"])
    end = datetime.combine(day + timedelta(days=1), datetime.min.time())
    title = f"Home internet — {day.strftime('%A')} {format_date(day)}"
    folder = Path(graphs_dir) / DAILY_DIR_NAME
    png = folder / f"{day.isoformat()}.png"
    page = folder / f"{day.isoformat()}.html"
    write_png(data, png, title, (start, end), is_week=False)
    write_html(data, page, title, (start, end), is_week=False, graphs_dir=graphs_dir)
    return [png, page]


def make_week_graphs(all_rows, last_day, graphs_dir, all_outages=None):
    first_day = last_day - timedelta(days=6)
    rows = rows_between(all_rows, first_day, last_day)
    if not rows:
        return []
    history = rows_between(all_rows, first_day - timedelta(days=analysis.BASELINE_DAYS - 1),
                           last_day)
    week_outages = outages.outages_between(all_outages or [], first_day, last_day)
    data = GraphData(rows, data_use_line(all_rows, last_day), history, week_outages)
    start = datetime.combine(first_day, datetime.min.time())
    end = datetime.combine(last_day + timedelta(days=1), datetime.min.time())
    title = f"Home internet — {format_date(first_day)} to {format_date(last_day)}"
    folder = Path(graphs_dir) / WEEK_DIR_NAME
    png = folder / "last-7-days.png"
    page = folder / "last-7-days.html"
    write_png(data, png, title, (start, end), is_week=True)
    write_html(data, page, title, (start, end), is_week=True, graphs_dir=graphs_dir)
    return [png, page]


def make_all(today, log_file, graphs_dir, one_day=None, outage_file=None):
    """Nightly and on-demand: the last 7 days (or one chosen day) plus the weekly view.

    Redrawing the last 7 days each time means a night missed while the laptop
    slept is filled in on the next run.
    """
    rows = logfile.read_rows(log_file)
    all_outages = []
    if outage_file is not None:
        all_outages = outages.find_outages(outages.read_checks(outage_file))
    written = []
    if one_day is not None:
        written.extend(make_day_graphs(rows, one_day, graphs_dir, all_outages))
        written.extend(make_week_graphs(rows, one_day, graphs_dir, all_outages))
        return written
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        written.extend(make_day_graphs(rows, day, graphs_dir, all_outages))
    written.extend(make_week_graphs(rows, today, graphs_dir, all_outages))
    if rows:
        week_start = today - timedelta(days=6)
        report_path = Path(graphs_dir) / report.REPORT_DIR_NAME / "last-7-days.html"
        written.append(report.write_report(rows, all_outages, week_start, today, report_path))
        ensure_plotly_js(graphs_dir)
        written.append(homepage.write_home(rows, all_outages, graphs_dir))
    return written


def main(argv):
    one_day = None
    if len(argv) > 1:
        one_day = date.fromisoformat(argv[1])
    written = make_all(date.today(), config.LOG_FILE, config.GRAPHS_DIR, one_day,
                       outage_file=config.OUTAGE_LOG_FILE)
    if not written:
        print("No logged tests to graph yet.")
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
