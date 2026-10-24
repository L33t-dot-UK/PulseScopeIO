"""Branded two-page offline report matching the supplied online PDF."""



import base64, io, re, math

from datetime import datetime

from zoneinfo import ZoneInfo

import numpy as np

from reportlab.pdfgen import canvas

from reportlab.lib import colors

from reportlab.lib.utils import ImageReader, simpleSplit

from reportlab.platypus import Table, TableStyle, Paragraph

from reportlab.lib.styles import ParagraphStyle

from xml.sax.saxutils import escape

from engine import ROOT, NAMES



W, H = 595.276, 841.89

INK = "#182944"

MUTED = "#596b86"

LINE = "#cbd6e6"

PANEL = "#f1f4f9"

DARK = "#0b1020"

PALETTE = ["#4dd6e8", "#ffb454", "#82ce78", "#c88bff", "#ff6178", "#ece060"]





def number(v, digits=5):

    return "-" if v is None or not math.isfinite(float(v)) else f"{v:.{digits}g}"





def engineering(value, unit, digits=5):

    """Use SI prefixes for physical measurements, retaining nonphysical units."""

    if value is None or not math.isfinite(float(value)):

        return "-"

    if unit not in ("s", "Hz", "V"):

        return number(value, digits) + " " + unit

    prefixes={-12:"p",-9:"n",-6:"µ",-3:"m",0:"",3:"k",6:"M",9:"G",12:"T"}

    exponent=max(-12,min(12,3*math.floor(math.log10(abs(value))/3))) if value else 0

    scaled=value/10**exponent

    if abs(float(f"{scaled:.{digits}g}"))>=1000 and exponent<12:

        exponent+=3;scaled=value/10**exponent

    return f"{scaled:.{digits}g} {prefixes[exponent]}{unit}"





def phase_summary(r):

    return (

        r.phase_degrees,

        r.phase_dt * (r.effective_time_scale or 1) if r.phase_dt is not None else None,

        r.phase_pairs,

    )





class Report:

    def __init__(self, path):

        self.c = canvas.Canvas(str(path), pagesize=(W, H))

        self.c.setTitle("Pulse Scope IO - Encoder pulse analysis report")

        self.c.setAuthor("Pulse Scope IO")



    def text(

        self, x, y, text, size=7, color=INK, bold=False, width=None, maxlines=None

    ):

        font = "Helvetica-Bold" if bold else "Helvetica"

        self.c.setFont(font, size)

        self.c.setFillColor(colors.HexColor(color))

        lines = simpleSplit(str(text), font, size, width) if width else [str(text)]

        for i, line in enumerate(lines[:maxlines] if maxlines else lines):

            self.c.drawString(x, H - y - size - i * size * 1.22, line)



    def rect(self, x, y, w, h, fill, stroke=None, radius=0):

        self.c.setFillColor(colors.HexColor(fill))

        self.c.setStrokeColor(colors.HexColor(stroke or fill))

        self.c.setLineWidth(0.6)

        if radius:

            self.c.roundRect(x, H - y - h, w, h, radius, fill=1, stroke=1)

        else:

            self.c.rect(x, H - y - h, w, h, fill=1, stroke=1)



    def line(self, x, y, x2, y2, color=LINE, width=0.5):

        self.c.setStrokeColor(colors.HexColor(color))

        self.c.setLineWidth(width)

        self.c.line(x, H - y, x2, H - y2)



    def section(self, title, y):

        self.text(28, y, title, 10.5, bold=True)

        self.line(28, y + 16, W - 28, y + 16)



    def footer(self, page, now):

        self.text(24, 16, now.strftime("%d/%m/%Y, %H:%M"), 7, color="#222222")

        self.text(

            270, 16, "Pulse Scope IO | Offline encoder analysis", 7, color="#222222"

        )

        self.text(24, H - 24, "Pulse Scope IO - Offline analysis", 7, color="#222222")

        self.text(W - 40, H - 24, f"{page}/2", 7, color="#222222")



    def chart(self, r, points, x, y, w, h, recovered=False):

        self.rect(x, y, w, h, DARK)

        channels = [6, 7, 8] if recovered else list(range(6))

        left = x + 23

        right = x + w - 5

        top = y + 20

        bottom = y + h - 25

        lane = (bottom - top) / len(channels)

        t0 = points[0][0]

        t1 = points[-1][1]

        span = t1 - t0 or 1

        for j, c in enumerate(channels):

            base = top + j * lane

            self.text(

                x + 3,

                base + lane * 0.35,

                ["A", "B", "Z"][c - 6] if recovered else NAMES[c],

                4.5,

                PALETTE[(c - 6) * 2] if recovered else PALETTE[c],

            )

            self.line(left, base + lane, right, base + lane, "#28344b")

            available = (c in r.available) if recovered else (c in r.present)

            if not available:

                continue

            minimum = 0 if recovered else min(p[2][c] for p in points)

            maximum = 1 if recovered else max(p[3][c] for p in points)

            amplitude = maximum - minimum or 1

            path = self.c.beginPath()

            last = None

            for p in points:

                px = left + (p[0] - t0) / span * (right - left)

                lo = p[4][c] if recovered else p[2][c]

                hi = p[5][c] if recovered else p[3][c]

                ya = base + lane - 3 - (lo - minimum) / amplitude * (lane - 7)

                yb = base + lane - 3 - (hi - minimum) / amplitude * (lane - 7)

                if last is None:

                    path.moveTo(px, H - ya)

                else:

                    if recovered:

                        path.lineTo(px, H - last)

                    path.lineTo(px, H - ya)

                path.lineTo(px, H - yb)

                last = yb

            self.c.setStrokeColor(

                colors.HexColor(PALETTE[(c - 6) * 2] if recovered else PALETTE[c])

            )

            self.c.setLineWidth(0.35)

            self.c.drawPath(path)

        for frac in [0, 0.25, 0.5, 0.75, 1]:

            px = left + frac * (right - left)

            self.line(px, top, px, bottom, "#28344b", 0.2)

            self.text(

                min(px, right - 40),

                bottom + 5,

                engineering(t0 + frac * span, "s" if r.seconds else "units"),

                4.5,

                MUTED,

            )



    def mini(self, r, c, x, y, w, h, kind, eye):

        m = next((m for m in r.metrics if m["c"] == c), None)

        self.rect(x, y, w, h, PANEL, LINE, 2)

        self.text(

            x + 5,

            y + 3,

            f"{NAMES[c]} " + ("eye diagram" if kind == "eye" else "period jitter"),

            8,

            bold=True,

        )

        self.rect(x, y + 16, w, h - 30, DARK)

        left = x + 14

        right = x + w - 4

        top = y + 29

        bottom = y + h - 26

        if m is None:

            self.text(x + w * 0.28, y + h * 0.47, "Channel not present", 4.5, MUTED)

        elif kind == "jitter":

            hist = m["hist"]

            maximum = max(1, max(hist))

            bw = (right - left) / 40

            for i, v in enumerate(hist):

                self.rect(

                    left + i * bw,

                    bottom - v / maximum * (bottom - top),

                    max(0.2, bw - 0.3),

                    v / maximum * (bottom - top),

                    PALETTE[c],

                )

            for frac, label in [(0, "-5%"), (0.5, "0%"), (1, "+5%")]:

                self.text(left + frac * (right - left) - 3, bottom + 3, label, 4, MUTED)

        else:

            traces = eye.get(str(c), [])

            if traces:

                low = min(p[1] for trace in traces for p in trace)

                high = max(p[1] for trace in traces for p in trace)

                span = high - low or 1

                self.c.saveState()

                self.c.setStrokeAlpha(0.22)

                self.c.setStrokeColor(

                    colors.HexColor("#ff82b5" if c < 4 and m["oos"] else PALETTE[c])

                )

                self.c.setLineWidth(0.35)

                for trace in traces:

                    path = self.c.beginPath()

                    for i, (t, v) in enumerate(trace):

                        px = left + t / 2 * (right - left)

                        py = bottom - (v - low) / span * (bottom - top)

                        if not i:

                            path.moveTo(px, H - py)

                        else:

                            path.lineTo(px, H - py)

                    self.c.drawPath(path)

                self.c.restoreState()

            else:

                self.text(x + w * 0.25, y + h * 0.47, "No complete cycles", 4.5, MUTED)

            for frac, label in (

                [(0, "-1 width"), (0.5, "0.5 width"), (1, "2 widths")]

                if c >= 4

                else [(0, "0 UI"), (0.5, "1 UI"), (1, "2 UI")]

            ):

                self.text(left + frac * (right - left) - 3, bottom + 3, label, 4, MUTED)

        caption = (

            "Channel not present"

            if m is None

            else (

                "OOS Cycles: excluded for Z channels"

                if c >= 4 and kind == "eye"

                else (

                    f"OOS Cycles: {m['oos']} of {max(0,m['edges']-1)} complete cycles"

                    if kind == "eye"

                    else f"Periods: {max(0,m['edges']-1)} | outside +/-5%: {m['outside']}"

                )

            )

        )

        self.text(

            x + 5,

            y + h - 11,

            caption,

            5.5,

            (

                "#a9293c"

                if m and c < 4 and m["oos"] and kind == "eye"

                else "#18794e" if m and c < 4 and kind == "eye" else MUTED

            ),

            bold=True,

            width=w - 10,

            maxlines=1,

        )





def create_report(r, target, serial=""):

    import copy



    diagnostic = r.diagnostics()

    r = copy.copy(r)

    r.metrics = r.display_metrics()

    report = Report(target)

    c = report.c

    try:

        now = datetime.now(ZoneInfo("Europe/London"))

    except Exception:

        now = datetime.now()

    report.footer(1, now)

    c.drawImage(
        ImageReader(str(ROOT / "static/pulse-scope-io-logo.png")),
        28, H - 73, 100, 100 / 3, mask="auto", preserveAspectRatio=True,
    )
    report.text(140, 36, "Pulse Scope IO", 17, bold=True)

    report.text(140, 59, "Encoder pulse analysis report", 8, MUTED)

    report.text(500, 42, "Generated", 6, MUTED)

    report.text(477, 51, now.strftime("%d/%m/%Y, %H:%M:%S"), 6, MUTED)

    report.line(28, 87, W - 28, 87, "#d8474c", 2)

    unit = diagnostic["display_unit"]

    width = W - 56

    source_label = getattr(r, "display_name", r.source.name)

    if getattr(r, "bin_timebase", {}).get("applied"):

        source_label = "Time-base adjusted | " + source_label

    if getattr(r, "bin_alignment", None):

        source_label = "Assumed 90-degree alignment (B shifted) | " + source_label

    if getattr(r, "merge_info", {}).get("refinement", {}).get("applied"):

        source_label = "B timing refined from reference | " + source_label

    report.text(

        28,

        98,

        f'Source: {source_label}     Encoder serial number: {serial or "Not entered"}',

        6.5,

        width=width,

    )

    report.text(

        28,

        112,

        f"Samples loaded: {r.count:,}     Samples analyzed: {r.count:,}     Analysis span: {engineering(r.end-r.start, unit)}",

        6.5,

        width=width,

    )

    report.text(

        28,

        126,

        f'Selected time range: {engineering(r.start, unit)} to {engineering(r.end, unit)}     Low / high thresholds: {number(r.low)} / {number(r.high)}     Smoothing: {r.window} samples     Smooth edges: {"On" if r.options.get("smooth_edges") else "Off"}',

        6.3,

        width=width,

    )

    report.section("Summary and diagnostics", 145)

    nonz = [m for m in r.metrics if m["c"] < 4]

    means = lambda key: (

        float(np.mean([m[key] for m in nonz if m[key] is not None]))

        if any(m[key] is not None for m in nonz)

        else None

    )

    phase, dt, pairs = phase_summary(r)

    samplerate = diagnostic["sample_rate"]

    cards = [

        ("Samples in span", f"{r.count:,}", ""),

        (

            "Capture sample rate",

            engineering(samplerate, "Hz"),

            (

                "Needs timestamps for physical sample rate"

                if samplerate is None

                else "Mean sample rate"

            ),

        ),

        ("Rotation speed", number(r.rpm, 6) + " RPM", r.rpm_source),

        (

            "RS422 Rx A pulses per revolution",

            number(r.ppr or None),

            "Need Z channel to count revolutions" if not r.ppr else "",

        ),

        ("Channel OOS Cycles (45-55% duty)", str(diagnostic["oos_cycles"]), ""),

        (

            "Selected span at speed",

            engineering((r.end - r.start) * (r.effective_time_scale or 1), unit),

            "",

        ),

        ("Mean duty cycle", number(diagnostic["mean_duty"]) + "%", ""),

        (

            "Mean period",

            engineering(diagnostic["mean_period"], unit),

            "Average of available non-Z channel periods",

        ),

        (

            "Angular period (RS422 Rx A)",

            number(360 / r.ppr if r.ppr else None) + " deg",

            "360 degrees / pulses per revolution",

        ),

        (

            "Pulses for 1 degree rotation",

            number(r.ppr / 360 if r.ppr else None) + " pulses",

            "Pulses per revolution / 360 degrees",

        ),

        (

            "A/B phase shift (positive = A leads)",

            number(phase) + " deg",

            (

                (

                    "Varying phase | "

                    if diagnostic.get("phase_coherence", 1) < 0.9

                    or diagnostic.get("phase_coverage", 1) < 0.8

                    else ""

                )

                + ("A leads B" if phase and phase > 0 else "B leads A")

                + f" | dt {engineering(dt, unit)} | {pairs} paired cycles"

                if phase is not None

                else "Need A and B edges"

            ),

        ),

    ]

    gap = 4

    cw = (width - gap * 3) / 4

    for i, (label, value, note) in enumerate(cards):

        x = 28 + (i % 4) * (cw + gap)

        y = 166 + (i // 4) * 43

        report.rect(x, y, cw, 40, PANEL, LINE, 2)

        report.text(x + 5, y + 4, label, 5.3, MUTED, width=cw - 10, maxlines=2)

        report.text(x + 5, y + 14, value, 8.5, bold=True)

        report.text(x + 5, y + 26, note, 5.2, MUTED, width=cw - 10, maxlines=2)

    report.text(28, 299, "Missed-pulse check", 8, bold=True)

    report.rect(28, 313, width, 36, "#fff0f1" if r.error_count else "#f4f7fb", LINE, 2)

    report.text(34, 317, "Missed pulses per revolution", 12, bold=True)

    missing_note = (

        f'{r.error_count} count discrepancies against expected PPR {r.options.get("ppr")}'

        if r.options.get("reference")

        else f"{r.error_count} count discrepancies; automatic modal reference {diagnostic['reference_ppr']}"

    )

    if not r.ppr:

        missing_note = "An A or -A / B or -B channel and two Z rising edges are required for this check."

    report.text(34, 337, missing_note, 6.5, MUTED, width=width - 12)

    report.text(

        28,

        354,

        "Duty tolerance is 45-55%; Z and -Z are excluded from report duty totals. Jitter uses successive rising-edge periods. A/B/Z metrics use reconstructed RS422 data where available.",

        5.5,

        MUTED,

        width=width,

    )

    z = r.validated_z

    a = r.replay_edges

    ws = r.start

    we = r.end

    window_note = (

        "A complete Z pulse was not found; the chart shows the full analysis span."

    )

    npulses = 0

    if len(z) > 1:

        origin = int(z[0])

        idx = int(np.searchsorted(a, origin, side="right"))

        last = min(len(a) - 1, idx + 31)

        if last >= idx:

            ws = float(r.data[origin, 0])

            we = float(r.data[a[last], 0])

            npulses = last - idx + 1

            window_note = (

                f"First positive Z pulse and next {npulses} available phase pulses."

            )

    if we <= ws:

        ws = r.start

        we = r.end

    plots = r.plot(ws, we, 1000)

    points = plots["points"]

    report.section(

        f"Combined waveform - Z pulse and next {npulses} A/-A/B/-B pulses per channel",

        378,

    )

    report.text(

        28,

        398,

        f"Waveform window: {engineering(ws, unit)} to {engineering(we, unit)} | {window_note}",

        5.5,

        MUTED,

        width=width,

    )

    report.chart(r, points, 28, 414, width, 192)

    report.section("RS422 Rx Output", 613)

    report.text(

        28,

        633,

        "Recovered A, B and Z logic from differential pairs; shown over the same time window as the six-channel waveform.",

        5.5,

        MUTED,

        width=width,

    )

    report.chart(r, points, 28, 645, width, 115, True)

    report.section("Channel eye diagrams", 767)

    report.text(

        28,

        787,

        "Pink traces and OOS counts identify duty outside 45-55%. Z and -Z are excluded from duty tolerance checks.",

        5.5,

        MUTED,

        width=width,

    )

    c.showPage()

    report.footer(2, now)

    eye = plots["eyes"]

    eye.update(r.z_eyes(0, r.count)[0])

    gap = 14

    mw = (width - gap * 2) / 3

    for ch in range(6):

        report.mini(

            r, ch, 28 + (ch % 3) * (mw + gap), 29 + (ch // 3) * 132, mw, 118, "eye", eye

        )

    report.section("Channel period-jitter histograms", 299)

    report.text(

        28,

        319,

        "Cycle-to-cycle rising-edge period deviations from each channel mean; all six use the same +/-5% scale.",

        5.5,

        MUTED,

    )

    for ch in range(6):

        report.mini(

            r,

            ch,

            28 + (ch % 3) * (mw + gap),

            332 + (ch // 3) * 113,

            mw,

            100,

            "jitter",

            eye,

        )

    report.section("Per-channel measurements", 558)

    report.text(

        28,

        578,

        "Rise and fall times use the selected 10-90% estimator; measurements cover the full recording.",

        5.5,

        MUTED,

    )

    headers = [

        "Signal",

        "Avg rise time\n(10-90%)",

        "Avg fall time\n(90-10%)",

        "High width",

        "Low width",

        "Period",

        "Pulses/rev",

        "Pulse frequency\n/ rate",

        "Duty\ncycle",

        "OOS cycles\n(45-55%)",

        "Rising\nedges",

    ]

    rows = [headers]

    style = ParagraphStyle(

        "cell",

        fontName="Helvetica",

        fontSize=5.1,

        leading=6.3,

        textColor=colors.HexColor(INK),

    )

    headerstyle = ParagraphStyle("head", parent=style, fontName="Helvetica-Bold")

    for ch in r.present:

        raw = next(m for m in r.metrics if m["c"] == ch)

        m = (

            next((m for m in r.metrics if m["c"] == 6 + ch // 2), raw)

            if ch % 2 == 0

            else raw

        )

        time = lambda v: engineering(v, unit)

        rows.append(

            [

                ("RS422 Rx " + NAMES[ch] if ch % 2 == 0 and m["c"] >= 6 else NAMES[ch]),

                time(m["rise"]),

                time(m["fall"]),

                time(m["high"]),

                time(m["low"]),

                time(m["period"]),

                number(m["ppr"]),

                engineering(m["frequency"], m["frequency_unit"]),

                "N/A" if m["c"] in (4, 5, 8) else number(m["duty"]) + "%",

                str(m["oos"]) if ch < 4 else "Excluded",

                str(m["edges"]),

            ]

        )

    formatted = [

        [

            Paragraph(

                escape(str(v)).replace("\n", "<br/>"), headerstyle if i == 0 else style

            )

            for v in row

        ]

        for i, row in enumerate(rows)

    ]

    weights = [0.9, 1, 1, 0.9, 0.9, 0.9, 0.65, 0.85, 0.55, 0.7, 0.65]

    table = Table(

        formatted,

        colWidths=[width * v / sum(weights) for v in weights],

        rowHeights=None,

    )

    table.setStyle(

        TableStyle(

            [

                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef6")),

                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),

                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

                ("LEFTPADDING", (0, 0), (-1, -1), 3),

                ("RIGHTPADDING", (0, 0), (-1, -1), 3),

                ("TOPPADDING", (0, 0), (-1, -1), 5),

                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),

            ]

        )

    )

    tw, th = table.wrap(width, 200)

    table.drawOn(c, 28, H - 592 - th)

    report.line(28, 600 + th, W - 28, 600 + th)

    report.text(230, 606 + th, f"Copyright {now.year} David Bradshaw", 6, MUTED)

    c.save()

