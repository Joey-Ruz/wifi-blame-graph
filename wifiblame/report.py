"""Provider report: one printable page summarizing a date range, ready to save
as a PDF from the browser and attach to a support chat or complaint.

Run with: .venv/bin/python -m wifiblame.report                        (last 7 days)
          .venv/bin/python -m wifiblame.report 2026-11-01 2026-11-21  (any range)
"""

import html
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

from wifiblame import analysis, config, logfile, outages

REPORT_DIR_NAME = "report"
WORST_HOURS_SHOWN = 3


def _date(day):
    return day.strftime("%m/%d/%Y")


def _time(moment):
    return moment.strftime("%I:%M %p").lstrip("0")


def _mbps(value):
    return "—" if value is None else f"{value:.0f} Mbps"


def _share(value, plan):
    if value is None or not plan:
        return ""
    return f"{value / plan * 100:.0f}% of plan"


def _rows_in_range(rows, first_day, last_day):
    selected = []
    for row in rows:
        if first_day <= analysis.row_day(row) <= last_day:
            selected.append(row)
    selected.sort(key=analysis.row_time)
    return selected


def _reliable_ok(rows):
    selected = []
    for row in rows:
        if row["status"] == logfile.STATUS_OK and not analysis.is_unreliable(row):
            selected.append(row)
    return selected


def _median_of(rows, field):
    values = []
    for row in rows:
        if row.get(field) is not None:
            values.append(row[field])
    if not values:
        return None
    return median(values)


def summarize(rows, all_outages, first_day, last_day, settings):
    """The report's numbers, separate from its layout so they can be tested."""
    in_range = _rows_in_range(rows, first_day, last_day)
    tested = []
    for row in in_range:
        if row["status"] in (logfile.STATUS_OK, logfile.STATUS_FAILED):
            tested.append(row)
    reliable = _reliable_ok(in_range)
    plan_down = settings["plan_download_mbps"]

    under_half_plan = None
    if plan_down and reliable:
        count = 0
        for row in reliable:
            if row.get("download_mbps") is not None and row["download_mbps"] < plan_down / 2:
                count += 1
        under_half_plan = count

    by_hour = {}
    for row in reliable:
        if row.get("download_mbps") is not None:
            by_hour.setdefault(analysis.row_time(row).hour, []).append(row["download_mbps"])
    hour_medians = []
    for hour, values in by_hour.items():
        if len(values) >= 2:
            hour_medians.append((median(values), hour, len(values)))
    hour_medians.sort()

    flagged = []
    anomalies = analysis.find_anomalies(in_range, rows)
    for index, reasons in anomalies.items():
        flagged.append((in_range[index], reasons))

    period_outages = outages.outages_between(all_outages, first_day, last_day)
    outage_minutes = {outages.KIND_PROVIDER: 0, outages.KIND_WIFI: 0}
    for outage in period_outages:
        outage_minutes[outage["kind"]] += outage["minutes"]

    days = []
    for day_rows in analysis.group_by_day(in_range).values():
        day = analysis.row_day(day_rows[0])
        day_reliable = _reliable_ok(day_rows)
        day_anomalies = 0
        for row, _reasons in flagged:
            if analysis.row_day(row) == day:
                day_anomalies += 1
        days.append({
            "day": day,
            "tests": len([r for r in day_rows if r["status"] in (logfile.STATUS_OK,
                                                                  logfile.STATUS_FAILED)]),
            "download": _median_of(day_reliable, "download_mbps"),
            "upload": _median_of(day_reliable, "upload_mbps"),
            "anomalies": day_anomalies,
            "outages": len(outages.outages_between(period_outages, day, day)),
        })
    days.sort(key=lambda item: item["day"])

    return {
        "tests": len(tested),
        "failed": len([r for r in tested if r["status"] == logfile.STATUS_FAILED]),
        "reliable": len(reliable),
        "typical_download": _median_of(reliable, "download_mbps"),
        "typical_upload": _median_of(reliable, "upload_mbps"),
        "lowest_download": min((r["download_mbps"] for r in reliable
                                if r.get("download_mbps") is not None), default=None),
        "highest_download": max((r["download_mbps"] for r in reliable
                                 if r.get("download_mbps") is not None), default=None),
        "under_half_plan": under_half_plan,
        "worst_hours": hour_medians[:WORST_HOURS_SHOWN],
        "router_delay": _median_of(reliable, "router_ping_ms"),
        "internet_delay": _median_of(reliable, "ping_ms"),
        "signal": _median_of(reliable, "signal_dbm"),
        "flagged": flagged,
        "outages": period_outages,
        "outage_minutes": outage_minutes,
        "days": days,
        "apple_tests": len([r for r in tested if r.get("tool") == "apple"]),
    }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin: 0 auto; max-width: 860px; padding: 24px 16px; background: #fff; color: #0b0b0b;
         font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 28px 0 8px; border-bottom: 1px solid #e1e0d9; padding-bottom: 4px; }}
  .sub {{ color: #52514e; margin: 0 0 16px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
  .tile {{ border: 1px solid #e1e0d9; border-radius: 8px; padding: 10px 12px; }}
  .tile b {{ display: block; font-size: 20px; }}
  .tile span {{ color: #52514e; font-size: 12px; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #e1e0d9; }}
  th {{ color: #52514e; font-weight: 600; }}
  .note {{ color: #52514e; font-size: 12px; }}
  a {{ color: #2a78d6; }}
  @media print {{ body {{ padding: 0; }} a {{ color: inherit; }} }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _tile(value, label):
    return f'<div class="tile"><b>{html.escape(value)}</b><span>{html.escape(label)}</span></div>'


def render(summary, first_day, last_day, settings):
    plan_down = settings["plan_download_mbps"]
    plan_up = settings["plan_upload_mbps"]
    parts = [f"<h1>Home internet report</h1>",
             f'<p class="sub">{_date(first_day)} to {_date(last_day)}'
             f' · {summary["tests"]} speed tests']
    if plan_down:
        plan_text = f"{plan_down:g} Mbps down"
        if plan_up:
            plan_text += f" / {plan_up:g} Mbps up"
        parts.append(f" · plan: {plan_text}")
    parts.append("</p>")

    parts.append('<div class="tiles">')
    down_label = "typical download"
    if plan_down and summary["typical_download"] is not None:
        down_label += f" ({_share(summary['typical_download'], plan_down)})"
    parts.append(_tile(_mbps(summary["typical_download"]), down_label))
    up_label = "typical upload"
    if plan_up and summary["typical_upload"] is not None:
        up_label += f" ({_share(summary['typical_upload'], plan_up)})"
    parts.append(_tile(_mbps(summary["typical_upload"]), up_label))
    parts.append(_tile(f"{_mbps(summary['lowest_download'])}", "slowest download"))
    if summary["under_half_plan"] is not None:
        parts.append(_tile(f"{summary['under_half_plan']} of {summary['reliable']}",
                           "tests under half the plan's download"))
    provider_minutes = summary["outage_minutes"][outages.KIND_PROVIDER]
    wifi_minutes = summary["outage_minutes"][outages.KIND_WIFI]
    parts.append(_tile(f"{provider_minutes} min", "provider outages"))
    parts.append(_tile(f"{wifi_minutes} min", "home Wi-Fi outages"))
    parts.append(_tile(str(summary["failed"]), "failed speed tests"))
    parts.append("</div>")

    parts.append("<h2>Is it the Wi-Fi or the provider?</h2><p>")
    context = []
    if summary["router_delay"] is not None:
        context.append(f"Typical delay to the home router was {summary['router_delay']:.1f} ms")
    if summary["internet_delay"] is not None:
        context.append(f"typical delay to the internet was {summary['internet_delay']:.1f} ms")
    if summary["signal"] is not None:
        context.append(f"typical Wi-Fi signal was {summary['signal']:.0f} dBm "
                       "(above -60 dBm is strong)")
    parts.append(html.escape("; ".join(context) + "." if context else "No readings yet."))
    parts.append(" Provider outages are times the home router answered but the internet "
                 "didn't; Wi-Fi outages are times the router itself stopped answering.</p>")

    if summary["worst_hours"]:
        parts.append("<h2>Slowest times of day</h2><table><tr><th>Hour</th>"
                     "<th>Typical download</th><th>Tests</th></tr>")
        for value, hour, count in summary["worst_hours"]:
            label = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
            share = _share(value, plan_down)
            share_text = f" ({share})" if share else ""
            parts.append(f"<tr><td>{label}</td><td>{_mbps(value)}{share_text}</td>"
                         f"<td>{count}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>Outages</h2>")
    if summary["outages"]:
        parts.append("<table><tr><th>Started</th><th>Lasted</th><th>Where</th></tr>")
        for outage in summary["outages"]:
            start = outage["start"]
            where = "Provider" if outage["kind"] == outages.KIND_PROVIDER else "Home Wi-Fi"
            parts.append(f"<tr><td>{_date(start.date())} {_time(start)}</td>"
                         f"<td>{outage['minutes']} min</td><td>{where}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>None recorded.</p>")

    parts.append("<h2>Slow and failed tests</h2>")
    if summary["flagged"]:
        parts.append("<table><tr><th>Time</th><th>Download</th><th>Upload</th>"
                     "<th>Why flagged</th><th>Result</th></tr>")
        for row, reasons in summary["flagged"]:
            moment = analysis.row_time(row)
            why = "; ".join(text for _kind, text in reasons)
            link = ""
            if row.get("result_url"):
                link = f'<a href="{html.escape(row["result_url"])}">speedtest.net</a>'
            parts.append(f"<tr><td>{_date(moment.date())} {_time(moment)}</td>"
                         f"<td>{_mbps(row.get('download_mbps'))}</td>"
                         f"<td>{_mbps(row.get('upload_mbps'))}</td>"
                         f"<td>{html.escape(why)}</td><td>{link}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>None.</p>")

    parts.append("<h2>Day by day</h2><table><tr><th>Day</th><th>Tests</th>"
                 "<th>Typical download</th><th>Typical upload</th><th>Flagged</th>"
                 "<th>Outages</th></tr>")
    for item in summary["days"]:
        parts.append(f"<tr><td>{item['day'].strftime('%a')} {_date(item['day'])}</td>"
                     f"<td>{item['tests']}</td><td>{_mbps(item['download'])}</td>"
                     f"<td>{_mbps(item['upload'])}</td><td>{item['anomalies']}</td>"
                     f"<td>{item['outages']}</td></tr>")
    parts.append("</table>")

    tool_text = "Ookla Speedtest"
    if summary["apple_tests"]:
        tool_text += (f" ({summary['apple_tests']} of the tests used Apple's built-in "
                      "networkQuality tester instead, when Ookla was unavailable)")
    parts.append('<p class="note">Measured over Wi-Fi from a Mac by wifi-blame-graph using '
                 f"{html.escape(tool_text)}. Tests taken on a VPN, in Low Power Mode, or while the Mac "
                 "was busy downloading are left out of the typical speeds. Wi-Fi usually "
                 "measures below a fast plan's full speed even when the provider delivers it.</p>")
    title = f"Home internet report {_date(first_day)} to {_date(last_day)}"
    return PAGE.format(title=html.escape(title), body="".join(parts))


def write_report(rows, all_outages, first_day, last_day, path, settings=None):
    if settings is None:
        settings = config.current()
    summary = summarize(rows, all_outages, first_day, last_day, settings)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(summary, first_day, last_day, settings))
    return path


def main(argv):
    if len(argv) == 3:
        first_day = date.fromisoformat(argv[1])
        last_day = date.fromisoformat(argv[2])
        name = f"report-{first_day.isoformat()}-to-{last_day.isoformat()}.html"
    elif len(argv) == 1:
        last_day = date.today()
        first_day = last_day - timedelta(days=6)
        name = "last-7-days.html"
    else:
        print(__doc__)
        return 1
    rows = logfile.read_rows(config.LOG_FILE)
    all_outages = outages.find_outages(outages.read_checks(config.OUTAGE_LOG_FILE))
    path = write_report(rows, all_outages, first_day, last_day,
                        config.GRAPHS_DIR / REPORT_DIR_NAME / name)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
