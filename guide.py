"""Build the searchable, linked Pulse Scope IO operator manual."""
from pathlib import Path
import base64
import io
import json
import re
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, Frame

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output/pdf/Encoder_Pulse_Viewer_User_Guide.pdf"
W, H = A4
NAVY = colors.HexColor('#142842')
BLUE = colors.HexColor('#086f8c')
RED = colors.HexColor('#b63d4c')
GREY = colors.HexColor('#52657b')
LIGHT = colors.HexColor('#edf3f8')


def fonts():
    candidates = [(Path('C:/Windows/Fonts'), 'arial.ttf', 'arialbd.ttf', 'consola.ttf'), (Path('/usr/share/fonts/truetype/dejavu'), 'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSansMono.ttf')]
    for folder, regular, bold, mono in candidates:
        if (folder / regular).exists():
            for name, file in [('Guide', regular), ('GuideBold', bold), ('GuideMono', mono)]:
                pdfmetrics.registerFont(TTFont(name, str(folder / file)))
            pdfmetrics.registerFontFamily('Guide', normal='Guide', bold='GuideBold', italic='Guide', boldItalic='GuideBold')
            return
    raise RuntimeError('Install Arial or DejaVu Sans to build the guide')


fonts()
BODY = ParagraphStyle('body', fontName='Guide', fontSize=9.3, leading=13.6, textColor=NAVY, spaceAfter=8)
SUB = ParagraphStyle('sub', parent=BODY, fontName='GuideBold', fontSize=11.3, leading=15, textColor=BLUE, spaceBefore=9, spaceAfter=6)
SMALL = ParagraphStyle('small', parent=BODY, fontSize=8.1, leading=11.5)
CODE = ParagraphStyle('code', parent=BODY, fontName='GuideMono', fontSize=8.1, leading=12.4, backColor=LIGHT, borderPadding=9, spaceBefore=5, spaceAfter=13)
CELL = ParagraphStyle('cell', parent=BODY, fontSize=8.2, leading=11.5, spaceAfter=0)
CHAPTERS = []


def chapter(key, title, strap, blocks, category='OPERATE'):
    CHAPTERS.append(dict(key=key, title=title, strap=strap, blocks=blocks, category=category))


def p(text): return ('p', text)
def h(text): return ('h', text)
def bullet(text): return ('p', '<bullet>&bull;</bullet>' + text)
def code(text): return ('code', text)
def table(rows, widths=None): return ('table', rows, widths)
def link(key, label): return f'<a href="#{key}" color="#086f8c">{label}</a>'
def external(url, label): return f'<a href="{url}" color="#086f8c">{label}</a>'


chapter('quick-start', 'Your first analysis', 'Start here if this is your first recording.', [
    p('Pulse Scope IO runs in your browser, with Python doing the analysis on your computer. It reads the complete recording, reconstructs A/B/Z logic, measures timing, identifies pulse-count discrepancies and replays antenna azimuth. It can combine two four-channel oscilloscope captures into a six-channel recording.'),
    h('1. Start the local service'),
    p('On Windows, double-click <b>Start-Viewer.bat</b>. On Linux, run <b>sh start-viewer.sh</b> from the application folder. Leave the terminal open. The default browser address is <b>http://127.0.0.1:8765/</b>; a server started with --port 8766 uses 8766 instead. '+link('windows-python', 'First-time installation is at the end of this guide.')),
    h('2. Choose a recording'),
    p('In <b>Load recordings</b>, select <b>CSV</b>, <b>CSV single channel</b> or <b>BIN + BXM</b>. CSV shows Browse recording and the four-channel merge controls; Browse recording also accepts TSV, text and supported WAV. CSV single channel and BIN show six optional channel rows, each with its own Browse button. See '+link('bin-loading','BIN/BXM loading')+'. You may enter a path and press Enter for a single CSV/WAV recording.'),
    h('3. Confirm interpretation'),
    p('Check the delimiter, time column and low/high thresholds. Keep the default 2.5 V thresholds for typical logic-level captures, or use Auto threshold. Check the detected sample rate and that the expected channels appear.'),
    h('4. Set the engineering reference'),
    p('If you know the encoder resolution, tick <b>Enable missing-pulse reference</b> and enter its A/B pulses per revolution, for example 8192. Enter the RPM if you want timing calibrated to that speed. Otherwise, clear RPM and let the viewer use physical Z timing where available. Your PPR reference stays separate from the count actually measured.'),
    h('5. Inspect the result'),
    p('Start with the missing-pulse total and the list of revolutions. Use <b>Zoom to error / Next</b> to see what was flagged, then look through the eye diagrams, jitter histograms and measurements. Unless you choose Visible span, the measurements cover the whole recording.'),
    h('6. Replay or export'),
    p('Use <b>Play</b> to replay pulse-counted azimuth. Add an encoder serial number and select <b>Export PDF</b>, or use <b>Download CSV</b> to save recording data. See '+link('exports','Exports and reports')+'.'),
])

chapter('workspace', 'The workspace and processing', 'Find your way around the viewer.', [
    table([['Area', 'Purpose'], ['Load recordings', 'Format selector; one multi-channel CSV, separate CSV channels, CSV merge or BIN channels. Progress and Cancel analysis are in this panel.'],
        ['Cache maintenance', 'Delete cache closes loaded recordings and removes cached data. The size is shown beside the button and updates automatically.'], ['Merge captures', 'Select capture A and capture B, plus an optional independent A/B alignment capture.'], ['Analysis settings', 'Thresholds, RPM and expected pulse count. Expand Import and threshold options for delimiter, time column and auto-threshold method.'], ['Save results', 'Encoder serial number, Download CSV and Export PDF.'], ['Header test data', 'Clean, missing, jitter and bearing-error generators; comedy button below. Differential option and seed are available.'], ['Waveforms and replay', 'Raw/recovered plots, error navigation, time-span controls and radar simulator.'], ['Diagnostic plots and table', 'Six channel eye cards, six jitter cards and numerical pulse measurements.']], [125, 377]),
    h('Whole file versus browser view'),
    p('The analysis covers every imported sample. Zoom in as far as you like: you are changing the view, not cutting data out of the fault search. For a dense recording, the browser draws a reduced set of points so the page stays responsive.'),
    p('<b>Whole recording</b> is the default measurement scope. <b>Visible span</b> recalculates measurements for the waveform span. PDF reports use the full recording. See '+link('time','Time, RPM and calibration')+' and '+link('waveforms','Waveform navigation')+'.'),
    h('Progress, dialogs and cancellation'),
    p('Imports, test-data creation, Auto threshold operations and export preparation show a loading dialog with a circular indicator. A percentage appears where available; an indeterminate ring means a percentage is not available for that stage. Cancel becomes available after an analysis job starts.'),
    p('Interactive actions such as sliders, zoom, pan and replay do not open a loading dialog. Settings trigger background reanalysis where applicable; there is no Reanalyse whole file button. Advanced waveform processing, recording-format help, replay help and measurement explanations expand only when needed. Let a job finish before opening another recording; the server permits one active analysis at a time. A cancellation or error is reported in the status area.'),
    h('Local operation'),
    p('The service listens on loopback, for use on the same computer. Processing and runtime assets are local. Internet access is needed for initial Python/package installation unless an offline installation is prepared. Closing the service stops the app; closing only the browser tab does not necessarily stop the Python process.'),
])

chapter('formats', 'Prepare recording files', 'Check the file layout before you load it.', [
    h('CSV, TSV and delimited text'),
    p('Use an optional header row followed by numeric rows. Unrecognised headings use positional channel assignment; see the CSV interpretation chapter. Comma, semicolon and tab separators are supported. Auto detect selects the separator; choose it explicitly if the preview or import result is wrong. Values must be finite, every row must have the same number of fields, and a selected time column must strictly increase.'),
    code('Time_s,A,-A,B,-B,Z,-Z\n0.000000,0,5,0,5,0,5\n0.000010,5,0,0,5,5,0\n0.000020,5,0,5,0,0,5'),
    p('The snippet illustrates format only; a few rows cannot establish encoder timing. Channel names may be A, -A, B, -B, Z, -Z or A+/A-, B+/B-, Z+/Z-. Unicode minus aliases are accepted. Ordinary single-file CSV imports may contain a subset of the six channels. Missing measurements are marked unavailable.'),
    p('Choose the input type using Recording format. BIN uses one raw channel file plus its same-stem BXM metadata; see '+link('bin-loading','BIN file requirements')+'.'),
    h('Time headings'),
    p('Explicit headings such as Time_s, Time_ms, Time_us and Time_ns are converted to seconds. Generic time, timestamp and t headings identify a time column but do not establish a physical unit. Without a selected time column, the app uses sample index. See '+link('time','time interpretation')+'.'),
    h('WAV and RF64'),
    p('A normal WAV import requires six channels in the order <b>A, -A, B, -B, Z, -Z</b>. Supported data is PCM 8/16/24/32-bit or IEEE float 32/64-bit, including supported extensible WAV and RF64 headers. The native WAV sample rate provides seconds. This is measurement data: a normal mono/stereo audio file is not a six-channel encoder recording.'),
    h('Four-channel capture inputs'),
    p('The merge workflow accepts four-channel WAV and CSV. For CSV, use signal names or CH1..CH4 / Channel1..Channel4 in the stated role order. Capture A is A/-A/Z/-Z; capture B is B/-B/Z/-Z; the optional alignment capture is A/-A/B/-B. Generic CH headings are interpreted by the selected capture role.'),
    p('<b>Limits:</b> CSV/WAV sources may be up to 10 GiB (10,737,418,240 bytes); BIN channel files and separate CSV channels each have a combined 10 GiB limit. A CSV header or numeric row may not exceed 1 MiB. Keep sources accessible and unchanged during import. Do not run the viewer directly inside its ZIP archive.'),
])

chapter('time', 'Time, RPM and calibration', 'Separate captured timing, entered speed and observed pulse count.', [
    table([['Setting or result', 'Meaning'], ['Time column', 'Selects the recorded time base, or sample index. Explicit s/ms/us/ns units establish physical time.'], ['Capture sample rate', 'A diagnostic derived from recording timing, or from native WAV metadata.'], ['RPM', 'Entered rotation speed; clear it to infer speed from physical Z timing if possible.'], ['Expected pulses/rev', 'A comparison reference used for missing/extra-pulse checks when enabled.'], ['Observed PPR', 'Mean active-phase rising-edge count between complete validated Z triggers.'], ['Replay Pulses/rev', 'Calibration for pulse-counted azimuth, especially when no complete Z interval exists.']], [142, 360]),
    h('RPM changes engineering time'),
    p('With an entered RPM and a usable Z interval, the app calibrates each average revolution to 60/RPM seconds. Displayed engineering widths, periods and frequencies follow that calibration. They can therefore differ from the raw capture time. The raw plot time axis still follows the imported recording coordinates.'),
    p('Clear RPM when you want physical seconds from a correctly labelled time column and automatic rotation-speed inference. A generic Time header or sample index alone cannot supply physical seconds. An entered RPM plus complete Z intervals can provide an engineering-time scale for those recordings.'),
    h('Worked example'),
    p('If Z repeats every one captured second and RPM is 60, one revolution is one engineering second. If you enter 30 RPM instead, the calibrated revolution is two seconds and an unchanged raw pulse width is displayed as twice its earlier engineering duration. Duty percentages on A/B do not change merely because all durations use the same scale.'),
    h('What needs a complete revolution'),
    p('Reliable Z-based PPR, RPM inference and revolution pulse-count checks need consecutive validated Z rising edges. Start/end fragments are not complete revolutions. A recording may contain many useful A/B edges while Z-based fields remain unavailable.'),
    p('Do not assume a reference count is measured truth. If all captured revolutions lose a similar number of pulses, a modal reference can hide a common deficit. Enable the known PPR reference for a definitive count comparison against the encoder setting. See '+link('missing','Missing pulses and revolution counts')+'.'),
])

chapter('thresholds', 'Thresholds and receiver logic', 'Turn voltage samples into edges without losing their meaning.', [
    h('Low and high thresholds'),
    p('Samples at or below the low threshold become logic 0. Samples at or above the high threshold become logic 1; values between the thresholds retain the previous state. At equal thresholds, a value must be strictly above the level to be high. Low must not exceed high. Threshold units follow the imported signal values.'),
    p('The usual default is 2.5 V. The app adapts its defaults for recognised normalised digital ranges. If your capture has another amplitude or offset, set appropriate thresholds or use Auto threshold. A correct file layout with incorrect thresholds can produce no edges, spurious edges or implausible measurements.'),
    h('Auto threshold choices'),
    table([['Method', 'Use'], ['Online candidate search (whole file)', 'Evaluates candidates against the recording. Useful for difficult levels; multiple full-file scans can be slow.'], ['Fast cluster midpoint', 'Uses the initial two-level estimate. A quicker starting point for large, clean captures.']], [175, 327]),
    h('Online RS422 receiver'),
    p('For a differential pair, the default receiver thresholds each side separately. When the two sides differ, the positive side determines the recovered logic. When both sides agree, it holds the last recovered state. This avoids inventing a transition merely because both sides temporarily occupy the same logic level.'),
    h('Voltage difference receiver and fallback'),
    p('The selectable difference receiver thresholds the differential input instead. If only one side is available, reconstructed logic falls back to the available signal; a negative-only input is inverted to recover its positive sense. Channel measurements use reconstructed pair data where available.'),
    h('A reliable setup sequence'),
    bullet('Start with the unsmoothed raw waveform and inspect both sides of each pair.'),
    bullet('Choose thresholds that separate the two stable levels. Confirm plausible recovered A/B/Z edges.'),
    bullet('Only then add smoothing if noise warrants it, and compare before/after edge counts.'),
    bullet('Use a known expected PPR to distinguish true count changes from an inference change.'),
    p('Threshold changes reanalyse the disk-backed samples. The optional third-capture phase check independently estimates levels on its reference capture; it is a comparison of recovered relative phase, not an analog voltage overlay.'),
])

chapter('smoothing', 'Smoothing and edge measurements', 'Use cleanup deliberately and understand rise/fall estimates.', [
    h('Smoothing average'),
    p('The average-samples slider applies a moving average before thresholding. Centered (online) averages around each sample; Causal uses current and earlier samples. A requested window is capped where necessary to preserve pulse spacing. The status text reports the effective setting.'),
    h('Smooth edges'),
    p('Smooth edges applies logic cleanup. The Adaptive (online) edge filter chooses a majority radius, up to six samples on each side; 3-sample majority is also available. Smoothing can remove isolated spikes but may also erase real short pulses if used inappropriately.'),
    p('When Smooth edges is selected, Non-thresholded raw plot is deselected and disabled. Clearing Smooth edges re-enables that checkbox but does not automatically select it. This keeps the display choice consistent with the active cleanup mode.'),
    h('Rise/fall estimator'),
    table([['Estimator', 'Interpretation'], ['Online crossing pairing', 'Pairs global 10% and 90% voltage crossings. It follows the online method, but noise can pair crossings across cycles and produce exaggerated slew.'], ['Local resolved ramps', 'Uses local plateau/interpolation logic over a short neighbourhood. An unresolved one-sample step can be unavailable rather than a credible analog slew.']], [140, 362]),
    h('Sampling limits'),
    p('A sampled edge does not reveal the exact analog transition between samples. Logic edge timing has sample resolution. A narrow pulse spanning only a few samples is much harder to measure than a long, well-resolved plateau. Use the raw plot and the scope sample rate to assess whether a numerical estimate is meaningful.'),
    h('Practical comparison'),
    p('Record the original pulse count and selected estimator, apply modest averaging, and compare count, high/low widths and eye shape. If smoothing alters expected pulses per revolution, inspect the affected region before accepting the cleaned result. Keep a CSV export of the original recording and include analysis settings with any interpretation.'),
    p('For a suspected missing pulse, '+link('waveforms','zoom to the error')+' and inspect both raw sides of the pair and the recovered output. A long low interval is different from a narrow voltage glitch, even if either can affect automatic edges.'),
])

chapter('waveforms', 'Navigate waveforms and faults', 'The raw and recovered plots share one time span.', [
    table([['Control', 'Action'], ['Channel checkboxes', 'Show or hide recorded A/-A/B/-B/Z/-Z channels.'],
        ['Recording navigator', 'Full-capture overview with the selected window highlighted. Click to jump or drag the box; zoom width is preserved.'], ['Drag across a waveform', 'A translucent cyan box follows the drag in either direction. Release to zoom both plots; cancellation clears the box.'], ['X-axis zoom', 'Adjust the horizontal scale.'], ['Scroll over a zoomed plot', 'Pan along the recording.'], ['Start / End + Apply span', 'Set an explicit finite interval inside the recording.'], ['Fit all / Full recording', 'Return to the full capture span.'], ['Start at first Z pulse', 'Move the selected analysis span to the first complete Z trigger; source data is retained.'], ['Hide flagged errors', 'Hide fault markers while keeping analysis results and the replay cursor.']], [155, 347]),
    p('The navigator readout gives the visible percentage, time interval and span. Its green line shows replay position. Dragging updates the detailed plots on release and pauses replay. Previous/Next window shifts 80% of the span; arrow keys shift 10%, Page Up/Down 90%, and Home/End go to recording boundaries.'),
    h('Zoom to error / Next'),
    p('This button navigates the indexed whole-recording faults and wraps after the last fault. The zoom shows approximately 16 actual A/B waveform cycles around the selected event. Details beside the button identify its channel and error type, with duty or observed/expected count details as applicable.'),
    p('Fault bands highlight out-of-spec A/B duty cycles and count discrepancies such as missing or extra pulses. Click a numbered marker to inspect the event. Count discrepancies cover a Z-to-Z interval; a flag on that interval does not by itself locate every individual absent edge inside it.'),
    h('Raw versus recovered'),
    p('The six-channel plot can draw original numeric values with Non-thresholded raw plot enabled. The RS422 Rx Output plot shows reconstructed positive-sense A, B and Z logic. Switching what is drawn does not change which source samples were imported.'),
    h('Replay position'),
    p('During playback, Follow replay when zoomed keeps both diagrams centred on the green marker while preserving the selected span. The window is clamped at recording boundaries and returns to the start when playback loops. Pause freezes the view. A dotted green vertical line marks the replay time on both waveform diagrams. It is a time cursor, not a fault flag. Its azimuth is determined from available pulses and Z resets. See '+link('replay','Position Simulator')+'.'),
    p('At large spans the waveform is an extrema overview rather than every individual sample. Zoom close enough to inspect pulse shape. Full-file measurements and fault indexing remain independent of the finite display budget.'),
])

chapter('measurements', 'Read the measurement table', 'Values reflect the selected scope, time calibration and estimator.', [
    table([['Measurement', 'Definition and important detail'], ['Average rise/fall time', 'Selected 10-90% / 90-10% estimator; analog resolution and noise matter.'], ['High / low width', 'Elapsed time between relevant opposite edges. Z timing uses validated revolution context.'], ['Period', 'Time between successive rising edges; index periods use validated Z triggers.'], ['Pulses/rev', 'Observed rising-edge count in complete validated revolutions, averaged over those revolutions.'], ['Pulse frequency / rate', 'Physical or calibrated rate where available; otherwise per sample/time unit.'], ['A/B duty cycle', 'High-state sample occupancy for the selected scope. Cycle-by-cycle duty is used for tolerance checks.'], ['Z/-Z duty cycle', 'Not applicable. The app does not calculate index duty; PDF reports show N/A.'], ['OOS Cycles (45-55%)', 'Number of A/B cycles outside the inclusive tolerance, excluding index channels.'], ['Rising edges', 'Count of relevant detected rising edges, which can include capture fragments.']], [142, 360]),
    h('Summary cards'),
    p('Cards include samples in span, capture sample rate, entered/inferred RPM, position PPR, OOS cycles, selected span at speed, mean duty and mean period, angular period, pulses for one degree and A/B phase. Aggregate duty and duty totals exclude Z/-Z. Available differential pairs also have mean peak-to-peak voltage cards; see '+link('differential-vpp','Differential peak-to-peak voltage')+'.'),
    h('Phase and angle'),
    p('A/B phase is the circular mean of B rising-edge position within complete local A cycles. Cycles with zero or multiple B edges are excluded. A leads B for positive phase. Low coherence or limited paired-cycle coverage is labelled; a single mean is then unreliable. Approximately +90 degrees and -90 degrees describe opposite quadrature senses.'),
    h('Why values differ'),
    p('Visible-span measurements can differ from whole-recording values. A/B duty occupancy is not necessarily the simple average of per-cycle duty. RPM calibration can alter engineering time and frequency. Partial cycles can contribute edges without providing a complete revolution. Always note scope and settings when comparing results.'),
    p('Unavailable is a data-condition result, not a zero. Check missing channels, time units, valid Z intervals and edge resolution before treating a blank measurement as a fault.'),
])

chapter('missing', 'Missing pulses by revolution', 'Distinguish the number of deficient intervals from the number of lost pulses.', [
    h('The pulse-count discrepancies box'),
    p('The first line shows the <b>total missing pulses</b>, the number of pulse-count discrepancies and the number of complete revolutions. A bullet for every complete Z-to-Z revolution gives the missing count, with A and B listed separately. Inverted channels are not counted again.'),
    table([['Example display', 'Meaning'], ['144 missing pulses', 'Sum of positive deficits on checked A and B phases in all complete validated revolutions.'], ['2 pulse-count discrepancies', 'Two channel/revolution comparisons differ from their reference; this is not two lost pulses.'], ['Revolution 1: 76 (A:76, B:0)', 'A is short by 76 pulses during this complete interval; B has no positive deficit.'], ['Revolution 2: 68 (A:68, B:0)', 'The next complete interval is short by 68 A pulses.']], [200, 302]),
    h('Choose the right reference'),
    p('With Enable missing-pulse reference selected, the expected PPR is compared with both available phases. Otherwise the app uses the most common observed count for each phase, choosing the larger count on a tie. The box labels an entered or inferred reference. Uniform losses across every revolution can therefore be hidden by inference.'),
    h('What is included'),
    p('Counts cover consecutive validated Z rising edges. Partial initial/final revolutions are excluded because their expected count is not a full PPR. For more than 200 complete revolutions, Previous/Next revolutions pages the bullets; the total still covers the entire recording.'),
    p('A deficit is max(expected minus observed, zero). An excess may produce an extra-pulse discrepancy under an entered reference, but is not added to the missing total. Z/-Z are revolution markers and are not counted as missing A/B pulses.'),
    h('Interpretation'),
    p('A count deficit can result from actual absent pulses, incorrect thresholds, noise, reverse motion or an incorrect PPR reference. Inspect the waveform and recovered logic before making a physical diagnosis. Z glitch filtering rejects a distinct narrow-width cluster only when enough normal complete index pulses support that distinction.'),
])

chapter('eyes', 'Eye diagrams and jitter histograms', 'Use repeated shapes to reveal timing variation and outliers.', [
    h('Layout and channel pairing'),
    p('The cards pair A above -A, B above -B and Z above -Z. Every recorded channel has an eye/detail card and a period-jitter histogram. A missing channel or insufficient complete cycles is explained in the card rather than replaced by fabricated data.'),
    h('A/B eye diagrams'),
    p('A/B eyes overlay up to 50 examples spanning two pulse periods. The grid, channel colour and trace count support comparison across cards. Pink overlays indicate cycles with duty outside 45-55%; normal traces retain their signal colour. The OOS badge is a count, not a percentage of displayed traces.'),
    h('Z and -Z index detail'),
    p('Index pulses are often narrow compared with one revolution. Their cards therefore use a three-reference-pulse-width detail view, not two full revolutions. Both polarities use the same positive-Z trigger and scale, preserving samples around transitions so the complementary shape can be inspected.'),
    p('If the current span lacks a complete eye cycle, the app can show a nearby complete cycle and labels that scope. A nearby-cycle eye is useful context but is not proof that the visible span itself contained that pulse. Duty is not calculated or tolerance-tested for Z/-Z.'),
    h('Period jitter'),
    p('Histograms show successive rising-edge period deviation from each channel mean. The common horizontal range is -5% to +5%, with 40 bins. Overflow badges identify periods outside that range; they are not silently included in the edge bins. A missing pulse can create a long period and therefore an overflow.'),
    h('A sensible review'),
    bullet('Check the channel and displayed scope before comparing two cards.'),
    bullet('Use a waveform zoom to distinguish analog noise from a real missing edge.'),
    bullet('Compare both polarities and recovered logic when an eye looks inconsistent.'),
    bullet('Treat a tidy eye as evidence about displayed cycles, not proof that every cycle is clean.'),
    p('The numerical measurement scope controls histogram/measurement interpretation. Full recording is the default; visible-span mode is available for a selected region.'),
])

chapter('replay', 'Replay antenna azimuth', 'Position follows pulse edges, with Z resetting North.', [
    h('Position Simulator'),
    p('Open the Position Simulator panel and select Play. The radar shows pulse-counted antenna azimuth and a pulse counter. Z rising edges reset azimuth to North (0 degrees). A dotted green waveform cursor follows the replay position on both diagrams.'),
    table([['Control or line', 'Behaviour'], ['Play / Pause', 'Start continuous replay or stop it at the current position. Playback loops at the end.'], ['Replay speed', '0.1x to 1.0x: 10% to 100% of the configured replay speed.'],
        ['Follow replay when zoomed', 'Enabled by default: both waveforms pan with the green cursor while preserving zoom width. Clear it for a fixed view.'], ['-1/-10/-20 pulses', 'Step backward by the selected number of active-channel pulses.'], ['+1/+10/+20 pulses', 'Step forward by the selected number of active-channel pulses.'], ['Pulses/rev', 'Set replay calibration when it cannot be derived from complete Z intervals; default is 8192.'], ['Solid green', 'Pulse-counted azimuth. Missing active pulses can make it lag.'], ['Dim dotted green', 'Expected azimuth based on Z timing, or estimated normal pulse timing without a complete Z interval. It follows the measured rotation direction.']], [145, 357]),
    h('Available-channel rules'),
    p('A edges drive rotation where available; B is the fallback, including recovery from negative-side inputs. The other quadrature phase supplies direction. A single available phase assumes clockwise rotation. Z-only replay is interpolation and is labelled accordingly; it does not imply measured A/B motion.'),
    h('RPM and elapsed time'),
    p('With RPM entered, a Z-to-Z revolution is replayed over 60/RPM seconds regardless of the captured sample count. The speed slider multiplies that elapsed-time model. With no complete Z interval, Pulses/rev provides the count-to-angle calibration. The dotted marker uses normal pulse timing estimated from the recording rather than treating entered RPM as its capture-time calibration. This avoids accumulated drift on clean no-Z captures. Unusually long pulse gaps can still create a difference between the markers; the estimate is not an independent missing-pulse reference. Every available Z rising edge still resets both positions to North.'),
    h('Investigating a discrepancy'),
    p('Pause, step through an area of interest and compare the solid pulse-counted line with the expected line. Use the waveform cursor and fault navigation to inspect actual captured edges. A reset at Z prevents accumulated angle error from carrying indefinitely across revolutions.'),
    p('Replay is a visual analysis aid. Direction depends on the available phase relationship and calibration; it is not a control output to a radar or motor.'),
])

chapter('merge', 'Merge two four-channel captures', 'Create all six channels using shared index pulses.', [
    table([['Input', 'Required channels in generic CH1..CH4 order'], ['Capture A', 'A, -A, Z, -Z'], ['Capture B', 'B, -B, Z, -Z'], ['Optional alignment capture', 'A, -A, B, -B; see the next chapter.']], [150, 352]),
    h('Prepare and merge'),
    p('Use identical nominal sample rates and uniform sampling for captures A and B, with compatible time units. Their timestamp origins and lengths may differ. Select each with Browse A... and Browse B..., then press Merge and analyse. Named CSV channels are preferred; generic CH1..CH4 or four-channel WAV use the order above.'),
    h('How synchronization works'),
    p('The first complete validated Z rising edges establish the reference. Later corresponding Z pulses synchronize each revolution. Already aligned captures retain their exact samples and shared overlap. If Z timing drifts, B/-B is linearly resampled between paired Z anchors onto the A time grid; output is limited to the verified Z span with complete endpoint pulses. Original files are kept.'),
    p('Merged A/-A and Z/-Z come from capture A. Merged B/-B come from capture B. The summary reports the initial offset, retained samples, matched Z events and source Z drift before correction. It labels corrected B timing. Missing Z events are skipped when their revolution numbers are unambiguous; ambiguous spacing is rejected. One matched Z cannot verify later alignment.'),
    h('Read the result'),
    p('All six channels feed plots, measurements, fault detection, replay and PDF reports. Download CSV writes a new time/A/-A/B/-B/Z/-Z recording that can be reopened normally. With a third capture, a dialog lets you choose an alignment check or B timing refinement. It does not add extra exported channels.'),
    h('Limits of independent acquisitions'),
    p('These captures form a composite, not six simultaneous oscilloscope channels. Changing speed, direction or pulse timing between acquisitions can make the composite disagree even with a valid initial Z alignment. Z synchronization corrects drift between index anchors; motion within a revolution may still differ. Review the optional A/B check and phase consistency before interpreting combined phase.'),
    p('The supplied examples/merge-A.csv and examples/merge-B.csv retain 4,963 samples with a -37-sample B offset. Changing delimiter/time-column import settings rereads the supplied sources; normal threshold/smoothing reanalysis reuses the merged samples.'),
])

chapter('alignment', 'Check alignment with a third capture', 'An independent A/B reference can have a different time base.', [
    h('Choose an optional reference'),
    p('Browse alignment... selects A/-A/B/-B in CSV/TSV or four-channel WAV. Named channels or CH1..CH4 / Channel1..Channel4 are accepted in that order. Clear alignment removes the optional input for the next merge. It is imported with independent delimiter/time detection and bounded, non-aliasing logic-level estimation. Scope time headings such as in s are recognized.'),
    h('Automatic scaling'),
    p('The reference sample rate, duration, start time and rotation speed may differ from the two primary captures. The check estimates a robust pulse period for each recording and scales the reference period to the merged period. It does not assume the same recording length represents the same number of revolutions.'),
    table([['Reported value', 'How to use it'], ['Reference time scale', 'Merged period divided by reference period, in their imported time units. A value of 2 stretches the reference timing by two.'], ['Sample scale', 'Merged samples per pulse period divided by reference samples per pulse period.'], ['Merged / reference phase', 'Independent signed A/B phase estimates after accounting for their pulse periods.'], ['Phase difference / tolerance', 'Wrapped angular difference, compared with a tolerance that includes sampling resolution.']], [160, 342]),
    h('Match, mismatch or inconclusive'),
    p('<b>Match</b> means relative A/B phase agrees within the reported tolerance. <b>Mismatch</b> means a coherent phase difference exceeds that tolerance. <b>Inconclusive</b> means there are too few useful edges, poor sampling resolution, inconsistent periods or unstable/noisy phase. At least five rising edges on each A and B channel are needed for the comparison.'),
    p('The result appears below the merge controls. A different phase sense, including swapped quadrature order, can be identified. The comparison uses the analysed Rx A/B cycles and refreshes after threshold/filter reanalysis. Phase uses B position within each local A cycle; cycles with missing or multiple B edges are excluded. Low coherence or low paired-cycle coverage makes the check inconclusive. Check alignment only preserves B timing. See '+link('alignment-choice','Choose how to use the third capture')+'.'),
    h('What this cannot prove'),
    p('The reference has no Z channel. Repeating A/B pulses cannot identify which absolute revolution, or which whole pulse cycle, was captured. A match confirms relative phase only, not absolute revolution identity or simultaneous capture timing. Use Z consistency and known scope setup alongside this check.'),
])

chapter('alignment-choice', 'Choose how to use the third capture', 'Make an explicit choice before the merge starts.', [
    h('The alignment-choice dialog'),
    p('Select captures A and B, add the optional A/-A/B/-B reference, then press Merge and analyse. A branded dialog explains the choices. No file processing starts until you choose. Without a third capture, the normal Z-synchronized merge starts directly.'),
    table([['Choice', 'Result'], ['Check alignment only', 'Recommended default. Keep the Z-aligned B timing and compare its phase with the independent reference. Waveforms and diagnostics can reveal differences.'], ['Refine B timing', 'Use a coherent reference phase to resample B/-B timing relative to A. The merged B waveform, measurements, replay and downloaded CSV change. Original captures are kept. A/-A/Z/-Z samples are unchanged within the verified complete-Z span; partial endpoints may be trimmed.'], ['Cancel / Escape', 'Close the dialog without starting a merge. The current recording stays open.']], [135, 367]),
    h('When refinement is appropriate'),
    p('Separate captures can differ in speed within a revolution. Z constrains their alignment at the index pulses; the third capture supplies the measured relative A/B phase. Refinement fits B timing to that phase, rather than assuming ideal 90-degree quadrature. It requires a stable, adequately sampled reference and unambiguous pulse correspondence.'),
    p('Refinement is refused if correspondence is ambiguous or if resampling would change pulse counts. Choose Check alignment only to inspect such captures. Refinement does not add missing pulses or establish an absolute revolution identity without Z in the reference.'),
    h('Recognize adjusted data'),
    p('The viewer labels B TIMING REFINED and shows a Reference-fit result. Agreement after fitting is constructed; it is not an independent proof of encoder phase or timing. Use the original captures or Check alignment only for an independent timing assessment.'),
    p('Refined downloads use an encoder-analysis-refined-B filename, and PDF reports label the adjusted timing. Keep that filename when sharing derived CSV data: the ordinary seven-column CSV does not store the full alignment history. Reanalysis retains the chosen mode; changing mode reimports the original captures.'),
])

chapter('generator', 'Generate repeatable test data', 'Exercise clean, missing-pulse and jitter behaviour without a scope.', [
    table([['Button or setting', 'Generated behaviour'], ['Generate test data', 'Nominal clean quadrature with index pulses.'], ['Generate test data with missing pulses', 'A-pulse losses spread throughout the capture; -A remains complementary. B/-B and Z/-Z remain intact.'], ['Generate Gaussian jitter data', 'Perturbs A/B edge timing with a nominal 1% period standard deviation, clipped to +/-3% of a period.'], ['Include differential channels', 'When selected, include all six channels; otherwise generate positive A, B and Z.'], ['Test seed', 'Reproduces the same pseudo-random pattern for the same kind and settings. Change it for a different test.']], [195, 307]),
    h('Common generated settings'),
    p('The browser generator uses 8192 pulses/revolution, three nominal revolutions and 60 RPM. It sets the display RPM to 60. The missing-pulse button enables the 8192 expected-PPR reference; the clean and jitter buttons leave that reference disabled. A generation/analysis dialog remains until processing completes.'),
    h('Sparse missing-pulse pattern'),
    p('The count is selected from 0.1% to strictly below 1% of nominal A pulses. At least 75% of lost pulses occur as isolated losses or pairs. There may be zero to three larger groups of 3-10 consecutive pulses. Random positions are spread through the capture; group separation prevents accidental joining into a longer run.'),
    p('With the default seed 20261020, 213 of 24,576 A pulses are omitted (about 0.8667%). The discrepancy box counts only complete validated revolutions, so its total can be smaller than the generator\'s total losses across the entire capture. This is expected when an end revolution lacks a complete closing index pulse.'),
    h('Suggested checks'),
    bullet('Generate clean data and confirm no A/B pulse-count deficits against the correct reference.'),
    bullet('Generate missing data, read the per-revolution bullets and inspect a waveform fault.'),
    bullet('Generate jitter data and compare period histograms with the clean capture.'),
    bullet('Repeat with differential channels disabled to exercise single-ended fallback.'),
    p('Synthetic examples establish known inputs. They do not guarantee the same noise, analog bandwidth or electrical behaviour as a physical encoder.'),
])

chapter('exports', 'Save CSV data and PDF reports', 'Save the recording, share a report or keep a renamed copy.', [
    h('Download CSV'),
    p('For an ordinary CSV/text import, Download CSV preserves the original source columns and numeric values. It does not export the selected zoom as a trimmed recording or replace source values with smoothed logic. WAV and BIN imports download decoded time and six channel columns. BIN exports include any time-base resampling or assumed B shift.'),
    p('For a merged recording, the export is the retained merged sample set: time, A, -A, B, -B, Z and -Z. It contains the applied alignment offset and the shared overlap, not just the first source file. An optional A/B reference is used for validation and is not substituted into the recording.'),
    h('Export PDF'),
    p('In Save results, enter an encoder serial number if required, then press Export PDF. The branded two-page analysis report includes metadata, diagnostic cards, missing-pulse checks, raw/recovered waveform excerpts, six eyes, six period-jitter histograms and channel measurements. Z/-Z duty is N/A. Engineering prefixes are used for time and frequency, such as ns, us, ms, Hz and kHz; voltage remains in volts.'),
    p('PDF reports cover the full recording, even when the browser measurement scope is Visible span. Report charts use a representative short waveform window beginning near the first validated Z and the next 32 active-phase pulses where available. They are not an image of every sample in a multi-gigabyte recording.'),
    h('Preparation and download'),
    p('Exports are prepared in the Python worker and streamed to the browser download. The loading dialog covers preparation; the browser then controls the final download location. Keep the server running until the download has completed. Large CSV exports can take time and need free disk space.'),
    h('Keep analysis context'),
    p('When you share a result, keep the recording and report together. Include the PPR reference, RPM, thresholds and any alignment warning so someone else can understand your settings. A CSV contains the recording; it does not save the whole browser session.'),
    h('This user guide'),
    p('This guide is a separate searchable PDF. Its contents, bookmarks, cross-references and alphabetical index navigate the instructions. Use your PDF reader\'s Find command (usually Ctrl+F) to search selectable text. External documentation links require internet access; application operation does not.'),
])

chapter('large-files', 'Large files and local storage', 'Full-file analysis uses disk-backed data and bounded browser responses.', [
    h('Capacity and expectations'),
    p('Each input source is limited to 10 GiB. Python streams CSV in bounded batches and stores decoded samples, reconstructed logic, edge indexes and other analysis data on disk. A small browser view is not an analysis truncation. Many closely spaced edges or noisy faults can still require substantial processing and storage.'),
    p('A recorded synthetic benchmark processed exactly 10,000,000,000 CSV bytes (69,444,444 samples) in about 255 seconds on the development machine. Its full-span plot took about 0.724 seconds. This is historical single-file evidence, not a timing guarantee. Two primary 10 GiB files plus a third reference have not been capacity-benchmarked together.'),
    h('Plan local SSD space'),
    p('Decoded samples use approximately 56 bytes per sample, before logic, edge, fault and replay indexes. Merging temporarily imports both primary captures before writing their shared composite; the optional reference needs additional scratch space. CSV text size is not a reliable predictor of the decoded size.'),
    h('Cache and original recordings'),
    p('The .cache directory is inside the application working folder. Reanalyses and jobs can leave retained caches. Original recording files are not modified by analysis. Export preparation also needs temporary space; completed export files are removed after streaming.'),
    p('Use <b>Delete cache</b> beside the displayed cache size in the workspace. The size refreshes about every ten seconds and after processing. Deletion closes loaded recordings and removes cached analyses, generated test recordings and cached upload copies. Original files outside .cache and completed downloads are kept.'),
    p('The button is disabled while analysis, export or cache operations are active. Finish or cancel analysis and wait for downloads to complete. Deletion resets the app; other open tabs detect the change and reload. Reopen a recording afterwards. If another process holds a cache file, the app reports the remaining size and you can retry. Do not delete the application folder or .venv as a cache-cleaning action.'),
    h('Performance choices'),
    bullet('Use a local SSD, a writable application folder and 64-bit Python.'),
    bullet('Use Fast cluster midpoint rather than full candidate search when an initial threshold estimate is sufficient.'),
    bullet('Let import/analysis finish before opening another capture. Use Cancel instead of terminating Python mid-write.'),
    bullet('Use waveform zoom to inspect detail; do not expect all 10 GiB to be rendered in the browser.'),
    p('The Linux workflow is documented, but this release\'s development validation was on Windows. Do a representative local acceptance test on your deployment machine.'),
])

chapter('workflows', 'Recommended analysis workflows', 'Three repeatable procedures for everyday use.', [
    h('A. Verify a known encoder capture'),
    bullet('Open the capture and confirm its time units, signal headings and physical voltage levels.'),
    bullet('Set a suitable threshold and use the expected encoder PPR, rather than inference, where known.'),
    bullet('Clear RPM for physical speed inference, or enter the intended RPM for calibrated engineering timing.'),
    bullet('Read total/per-revolution deficits, inspect the first fault, then navigate subsequent faults.'),
    bullet('Check both polarities, recovered logic, eye traces and jitter. Export a full-file report and retain the CSV.'),
    h('B. Build six channels from a four-channel scope'),
    bullet('Capture A/-A/Z/-Z, then B/-B/Z/-Z at the same sample rate and comparable operating conditions.'),
    bullet('Optionally acquire A/-A/B/-B as an independent relative-phase reference; its time base can differ.'),
    bullet('Select the inputs in their correct roles and run Merge and analyse.'),
    bullet('Read Z offset/drift warnings and the optional alignment status. A mismatch needs investigation, not blind acceptance.'),
    bullet('Download the merged six-channel CSV and identify it as a composite of independent acquisitions.'),
    h('C. Validate settings using generated data'),
    bullet('Generate clean data with a recorded seed, then missing-pulse and jitter data using known settings.'),
    bullet('Confirm that missing counts appear per complete revolution and that duty faults exclude Z/-Z.'),
    bullet('Test zoom-to-error, pulse stepping, replay looping and the 0.1x-1.0x speed range.'),
    bullet('Compare raw and cleaned results to learn how thresholds and smoothing affect your analysis.'),
    p('For a numerical comparison between sessions, record the measurement scope, PPR reference, RPM, thresholds, smoothing mode/window, receiver and rise/fall estimator. These settings can explain a result difference even when the source file is unchanged.'),
])

chapter('troubleshooting', 'Troubleshooting', 'Start with file interpretation, then timing, then analysis settings.', [
    table([['Symptom', 'Check or action'], ['Browser cannot connect', 'Start the service and use its printed 127.0.0.1 address/port. Keep its terminal open. See installation chapters.'], ['Invalid local session token / failed fetch', 'If the service restarted, refresh the browser and reopen the recording. A stopped or cancelled job needs a new load.'], ['No channel headings recognised', 'Check delimiter and the amber positional-assignment notice. Unrecognised columns use time first, then A, -A, B, -B, Z, -Z.'], ['No complete Z / unavailable RPM or PPR', 'Inspect Z/-Z polarity, levels and thresholds; include two complete validated index triggers.'], ['Unexpected missing-pulse count', 'Verify expected PPR, complete-revolution scope, thresholds, rotation direction and Z validity.'], ['Eyes not showing visible cycles', 'Read the scope/insufficient-data label; zoom out or include a complete pulse. Nearby-cycle eyes are explicitly labelled.'], ['Z duty is Not applicable', 'Intentional. Z/-Z duty is not calculated; widths and index period remain available.'], ['Rise/fall time seems unrealistic', 'Compare estimator modes and raw edge resolution; global crossing pairing can span noisy cycles.'], ['Merged files rejected', 'A and B require compatible uniform sample rates/time units and correct role channels.'], ['Alignment mismatch / inconclusive', 'Check A/B wiring/order and speed stability, or use a longer, better-sampled reference. No Z means absolute cycle identity is unavailable.'], ['Import is slow or disk is full', 'Use a local SSD, free space in the app/cache/download locations, and avoid full candidate searches unless needed.'], ['Native Browse fails', 'Windows uses its native dialog; Linux needs Tk and a desktop session. Check the displayed picker error and selected input format.']], [148, 354]),
    p('If a problem remains, retain the exact recording, settings, error text and a screenshot of the relevant view. Run a generated clean capture to separate a file-specific interpretation problem from a general installation problem. See '+link('windows-app','Windows app setup')+' or '+link('linux-app','Linux app setup')+'.'),
])

chapter('glossary', 'Glossary and interpretation limits', 'Use the same terms when comparing captures and reports.', [
    table([['Term', 'Meaning in this tool'], ['A / B', 'Quadrature phase channels; their relative order supplies direction.'], ['-A / -B / -Z', 'Negative/complementary sides of differential pairs, not additional independent encoder phases.'], ['Z / index', 'Once-per-revolution reference used to reset azimuth and define complete count intervals.'], ['PPR', 'Rising-edge pulses per revolution; do not substitute a manufacturer\'s quadrature x4 count without conversion.'], ['RS422 Rx', 'Positive-sense logic reconstructed from an available differential pair or single-ended fallback.'], ['Duty', 'High-state fraction; A/B table occupancy differs from per-cycle tolerance evaluation. Index duty is not calculated.'], ['OOS', 'Out of specification: here, an A/B cycle outside the 45-55% duty tolerance.'], ['Jitter', 'Cycle-to-cycle rising-edge period deviation relative to the channel mean.'], ['Complete revolution', 'Interval between consecutive validated Z rising edges. Capture fragments are excluded.'], ['Discrepancy', 'A channel/revolution pulse-count comparison that differs from its reference.'], ['Composite', 'A merged recording built from independent acquisitions, rather than simultaneous six-channel sampling.'], ['Alignment match', 'Independent A/B relative phase agrees within tolerance after period scaling; not proof of absolute cycle identity.'], ['Loopback', 'The same-computer network address, normally 127.0.0.1.'], ['Virtual environment', 'A local Python environment containing this app\'s dependencies.']], [142, 360]),
    h('What a result can establish'),
    p('The tool describes the imported samples using the selected reconstruction, scope and references. Faults require engineering interpretation: a sample-level observation is not proof of a physical encoder failure. Synthetic tests, Windows checks and historical capacity measurements are evidence for stated cases, not a universal guarantee for every capture or platform.'),
    p('The next pages provide a linked alphabetical index. Installation instructions follow it at the end of the guide, in Python-then-application order for Windows and Linux.'),
])

# The index is inserted before the installation chapters during layout.
chapter('windows-python', 'Windows: install Python', 'Install a 64-bit runtime before setting up the application.', [
    h('1. Choose a supported interpreter'),
    p('Use a 64-bit Python 3.12 or newer interpreter. Python 3.12 is the development baseline for this application. Prefer that baseline for a first deployment, then test newer versions with the dependency set. No online account is needed to run the app.'),
    h('2. Install from Python.org'),
    p('Open '+external('https://www.python.org/downloads/','Python.org downloads')+' and install the official Python Install Manager, or use an appropriate traditional 64-bit installer. With a traditional installer, include pip and the launcher; enable its PATH option if you plan to use python directly. Open a new PowerShell window after installation.'),
    p('With the install manager, select/install the 3.12 runtime explicitly. These commands use the manager/launcher rather than relying on whichever python alias happens to be first:'),
    code('py install 3.12\npy -3.12 --version\npy -3.12 -c "import struct; print(struct.calcsize(\'P\') * 8)"\npy -3.12 -m pip --version'),
    p('The architecture check should print 64. <b>py install</b> is an Install Manager command. A legacy launcher does not implement it: install the runtime through the official installer instead, then use py -3.12 to select it.'),
    h('3. Check Windows integration'),
    p('Windows Browse uses the native Windows file dialog through Python ctypes; Tkinter is not required. After starting the app, use Browse to verify it. If py is unavailable, use the full path to the intended python.exe; do not assume a Windows Store alias is the installed interpreter.'),
    h('Before continuing'),
    p('Download/extract EncoderPulseViewer-Offline.zip to a writable local folder, for example C:\\EncoderPulseViewer. Keep the whole project together. Choose a disk with adequate cache space. Continue to '+link('windows-app','Windows: install and run the app')+'.'),
    p('Official reference: '+external('https://docs.python.org/3/using/windows.html','Python on Windows')+'. Installation commands and available packages can evolve; this guide targets the stated runtime and app layout.'),
], 'INSTALL')

chapter('windows-app', 'Windows: install and run the app', 'Set up the app once, then use the launcher.', [
    h('1. Open PowerShell in the extracted folder'),
    p('First extract the ZIP. Open PowerShell and change to the folder containing server.py. Replace the example path below with your own, keeping the quotes if it contains spaces.'),
    code('Set-Location -LiteralPath "C:\\EncoderPulseViewer"\npy -3.12 -m venv .venv\n& .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip\n& .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt'),
    p('These commands create a .venv folder and install NumPy and ReportLab inside it. They use that copy of Python directly, so you do not need to activate anything. Setup.ps1 is another way to do the setup. The virtual-environment chapter explains both approaches.'),
    h('2. Verify dependencies and launch'),
    code('& .\\.venv\\Scripts\\python.exe -c "import numpy, reportlab; print(\'Dependencies OK\')"\n& .\\.venv\\Scripts\\python.exe server.py'),
    p('Your browser should open at http://127.0.0.1:8765/. Leave the terminal open while you use the viewer. Next time, double-click Start-Viewer.bat: it uses the .venv you just created and tells you if setup is incomplete.'),
    h('3. If the port is already in use'),
    code('& .\\.venv\\Scripts\\python.exe server.py --port 8766\n# Optional: suppress automatic browser launch\n& .\\.venv\\Scripts\\python.exe server.py --port 8766 --no-browser'),
    p('With --no-browser, open the address printed in the terminal yourself. If a port is busy, choose another one. Press Ctrl+C in the terminal when you want to stop the viewer; refresh the browser after starting it again.'),
    h('4. Complete a smoke test'),
    p('Before opening an important capture, try the clean test data and check that its channels and pulse counts look sensible. Then generate missing-pulse data, read the revolution list, and try saving a PDF and CSV. That gives you a quick check that the main tools are working. See '+link('quick-start','Your first analysis')+'.'),
    p('If installation must be disconnected, use '+link('offline-install','Offline installation and maintenance')+' instead of the internet pip steps. Native executables and service installers are not provided by this Python package.'),
], 'INSTALL')

chapter('linux-python', 'Linux: install Python', 'Install Python and the tools needed for file browsing.', [
    h('1. Use a 64-bit desktop environment'),
    p('Use Python 3.12 or newer on a 64-bit Linux system. A desktop browser is required for the interface; native Browse also needs Tk and an accessible desktop display. The instructions below cover common package-manager families. Package names/default versions depend on the distribution release.'),
    h('2. Ubuntu / Debian family'),
    code('sudo apt update\nsudo apt install python3 python3-venv python3-pip python3-tk unzip\npython3 --version\npython3 -c "import struct; print(struct.calcsize(\'P\') * 8)"'),
    p('Check that the version is at least 3.12 and architecture is 64. Ubuntu 24.04 provides a 3.12 runtime family. If your distribution supplies an older Python, use a supported newer distribution or install an appropriate parallel runtime using that distribution\'s documented procedure. Do not replace the system python3 executable.'),
    h('Fedora / related distributions'),
    code('sudo dnf install python3 python3-pip python3-tkinter unzip\npython3 --version\npython3 -m venv --help'),
    p('If your package names or Python version differ, follow your distribution\'s package documentation. Other distributions need an equivalent interpreter, venv support, pip and Tk. Do not use sudo pip to install application dependencies into the operating system Python.'),
    h('3. Verify Tk and continue'),
    code('python3 -m tkinter'),
    p('Close the Tk test window after it opens. On a headless session without a display, a native picker cannot open; enter a path in the local browser interface instead, or use a local desktop. The application service is loopback-only; remote hosting is not configured by these instructions.'),
    p('Official references: '+external('https://docs.python.org/3/using/unix.html','Python on Unix')+', '+external('https://ubuntu.com/developers/docs/howto/python-setup/','Ubuntu Python setup')+' and '+external('https://docs.python.org/3/library/venv.html','Python virtual environments')+'. Continue with '+link('linux-app','Linux: install and run the app')+'.'),
], 'INSTALL')

chapter('linux-app', 'Linux: install and run the app', 'Set up your own copy and check that it runs.', [
    h('1. Extract the application'),
    p('Put the ZIP in Downloads or substitute its actual path. Extract to a writable local folder with space for .cache and exports. The exact archive layout may create a subfolder; enter the folder containing server.py and requirements.txt.'),
    code('mkdir -p "$HOME/EncoderPulseViewer"\nunzip "$HOME/Downloads/EncoderPulseViewer-Offline.zip" \\\n  -d "$HOME/EncoderPulseViewer"\ncd "$HOME/EncoderPulseViewer"'),
    h('2. Install in a virtual environment'),
    code('python3 -m venv .venv\n.venv/bin/python -m pip install --upgrade pip\n.venv/bin/python -m pip install -r requirements.txt\n.venv/bin/python -c "import numpy, reportlab; print(\'Dependencies OK\')"'),
    p('If you selected a parallel interpreter, use that explicit executable for the venv command. There is no need to activate the environment when commands use .venv/bin/python directly. This also avoids externally-managed-environment errors on distributions that protect system Python.'),
    h('3. Launch the service'),
    code('sh start-viewer.sh\n# Or launch directly on another port:\n.venv/bin/python server.py --port 8766 --no-browser'),
    p('The default launcher uses port 8765 and normally opens a browser. With --no-browser, open the printed http://127.0.0.1:PORT/ address manually on the same machine. Keep the terminal running. Ctrl+C stops the service. If executing ./start-viewer.sh directly, set its executable bit with chmod +x start-viewer.sh first.'),
    h('4. Verify operation'),
    p('Generate clean, missing and jitter recordings; test the native file picker or full-path input; inspect all plotted channels and export both CSV and PDF. Check a representative real capture and a four-channel merge before deployment. This Linux procedure is documented but was not independently exercised on Linux in this development session.'),
    p('If venv creation reports ensurepip/venv missing, install the matching distribution venv package. If Browse reports Tk missing, install the matching Tk package. If the page will not open, first check that the terminal is still running and that you are using the address it prints.'),
], 'INSTALL')

chapter('offline-install', 'Offline installation and maintenance', 'Take the packages with you when internet access is unavailable.', [
    h('1. Prepare a matching connected machine'),
    p('Use the same operating system, CPU architecture and Python minor version as the disconnected target. Copy the application ZIP and the appropriate official Python installer or distribution packages. Prepare Windows and Linux wheelhouses separately; a Windows wheel is not a Linux package.'),
    code('# Run using the intended Python on the connected machine:\npython -m pip download --only-binary=:all: \\\n  -r requirements.txt -d wheelhouse'),
    p('On PowerShell, run that command on one line; the backslash above is a Linux shell continuation. Transfer the wheelhouse folder, application and Python installation media to the target using your normal approved transfer process. Linux OS packages may require additional dependent packages from a matching repository mirror.'),
    h('2. Install the runtime, then the app without internet'),
    p('Install Python as described in the platform chapters; native Browse on Linux also needs Tk and a desktop session. Create .venv, then use the appropriate environment interpreter:'),
    code('# Windows PowerShell (one line):\n& .\\.venv\\Scripts\\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.txt\n\n# Linux (one line):\n.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements.txt'),
    p('No package downloads should be needed when the wheelhouse matches the target and contains all dependencies. If a distribution has no matching wheel, resolve that on the connected preparation system before transferring. The app\'s own runtime resources are local once installed.'),
    h('3. Update and recover'),
    p('Stop the viewer, retain original recordings and exported results, and extract an updated application into a new folder. Recreate its virtual environment and install its requirements; do not copy an existing .venv between operating systems or Python installations. Re-run the generated-data and real-capture smoke tests before replacing the previous working copy.'),
    h('Optional technical verification'),
    code('# Connected environment, using the project interpreter:\npython -m pip install -r requirements-dev.txt\npython -m unittest discover -s tests -v'),
    p('The developer test suite adds pypdf and includes local HTTP checks. It is optional for operators and does not replace a local capture acceptance test. Retain this guide with the application distribution. Return to '+link('contents','Contents')+' or '+link('quick-start','Your first analysis')+'.'),
], 'INSTALL')



chapter('bin-loading', 'Load BIN and BXM recordings', 'Choose the channels you have; keep each BXM beside its BIN.', [
    h('Choose BIN mode'),
    p('In Load recordings, set Recording format to <b>BIN + BXM</b>. The CSV single-file and merge controls disappear. Six rows appear: A, -A, B, -B, Z and -Z. Select one to six files and leave unused channels blank. Each Browse button opens a BIN-specific native picker for .bin and .BIN files; Clear removes that row selection.'),
    h('Keep each metadata pair together'),
    p('Keep each BIN beside its matching BXM, for example 1_A.BIN and 1_A.BXM. You only browse for the BIN; the viewer finds the metadata alongside it. Choose the row for the channel you actually recorded, regardless of the scope’s C1/C2 label. Leave your originals unchanged.'),
    h('Supported binary format'),
    p('The supported scope layout is single-channel UINT16, either byte order. BXM is XML containing RecordLength, XStart/XStop in seconds, BaseYStart/BaseYStop in volts, MSBFirst and single-trace attributes. File length must equal two bytes per sample. Other raw formats are rejected rather than guessed.'),
    h('Load and inspect'),
    p('Press <b>Load BIN channels</b>. Import and analysis cover all samples, using disk-backed blocks. BXM supplies timing and voltage scale. Missing channels remain unavailable. Original files are not modified. The combined BIN input limit is 10 GiB; decoded data and indexes require extra disk space.'),
    p('Missing/invalid metadata and unusable files appear in a red error below the channel list. Differing valid time bases are adjusted automatically and shown in amber: '+link('bin-timebase','Time-base adjustment')+'. Optional assumed quadrature alignment is a separate choice: '+link('bin-quadrature','Assume 90-degree A/B alignment')+'.'),
    p('BIN data feeds the same waveforms, receiver logic, measurements, replay and reports as CSV. Download CSV writes decoded time plus all six channel columns; it does not copy raw BIN bytes.'),
])
chapter('bin-timebase', 'BIN time-base adjustment', 'Understand the amber message when capture timings differ.', [
    h('Reference timeline'),
    p('A supplies the reference sample grid when selected; otherwise the first selected channel supplies it. The BXM interval is (XStop - XStart) / (RecordLength - 1). Each capture is placed on an elapsed-time origin and the common available duration is retained.'),
    h('Different sample rates'),
    p('Channels with a different sample interval are mapped onto the reference grid by nearest-sample resampling. For example, a 5,000 samples/s B recording can be shown on a 10,000 samples/s A timeline. Samples are repeated or skipped as required; no voltage ramps are invented. Matching recordings keep direct sample mapping.'),
    h('Amber warning'),
    p('Whenever rates are scaled, origins rebased or durations trimmed, an amber Time-base adjustment applied notice appears below the BIN options. It lists the reference channel, source and target sample rates, rebased channel names and trimming. The adjustment information survives reanalysis; PDF source descriptions identify time-base-adjusted recordings.'),
    h('What alignment means here'),
    p('Rebasing the first samples does <b>not</b> prove that independent captures began at the same physical angle or revolution. Automatic time-base adjustment does not force A/B phase. Use '+link('bin-quadrature','the optional assumed 90-degree setting')+' if that assumption is appropriate; use the CSV Z-based merge workflow when shared index pulses are your reference.'),
    h('Measurement consequences'),
    p('Resampling puts edges on a different sample grid. That can change measured widths, duty or slew, and a lower sample rate can lose narrow details. Your original BIN/BXM files stay unchanged. Keep them for comparison and mention the adjustment when sharing results.'),
])
chapter('bin-quadrature', 'Assume 90-degree BIN alignment', 'Use this only when you are happy to assume a 90-degree relationship.', [
    h('Enable before loading'),
    p('Select <b>Assume 90-degree A/B alignment for separate captures</b>, then press Load BIN channels. A and B are required. Both differential partners, if present, must belong to their respective capture. Z/-Z must belong to the A capture. Different time bases are handled first.'),
    h('Constant B shift'),
    p('The viewer chooses the nearer +90 or -90 degree sense and shifts B/-B together by a constant whole-sample offset. It does not interpolate or stretch pulses in this alignment step. Unsupported endpoint samples are trimmed rather than wrapped or padded. Complete retained pulse widths and duty cycles are preserved by the shift.'),
    h('Sample resolution and resampling'),
    p('Exactly 90 degrees may fall between samples. The achieved measured phase is shown beside the target and B shift; for example -89.80 degrees can be the nearest whole-sample result. If automatic time-base resampling was also necessary, its sampling effects still apply; the constant-shift guarantee does not undo prior resampling.'),
    h('Quality checks'),
    p('Enough resolved, coherent A/B cycles and compatible rotation speeds are required. If a single shift cannot give a stable quadrature relationship, loading reports a red error instead of altering cycle lengths. Ordinary jitter is considered separately from sustained speed drift.'),
    h('Assumed result, not independent proof'),
    p('The amber alignment text identifies the target, measured phase and shift, and explicitly says that alignment was assumed. Plots, measurements, replay and downloaded CSV use the adjusted B samples; PDF sources are labelled. Original capture files remain unchanged. This mode cannot establish actual absolute phase, revolution identity or a whole-cycle offset between independent captures.'),
    p('The checkbox takes effect on Load BIN channels. It is separate from the third-reference CSV merge dialog and its reference-derived B timing refinement, described in '+link('alignment-choice','Choose how to use the third capture')+'.'),
])
chapter('differential-vpp', 'Differential peak-to-peak voltage', 'Check the voltage swing across each captured pair.', [
    h('Where it appears'),
    p('Summary cards show A/-A, B/-B and Z/-Z mean peak-to-peak voltage when both members of a pair are available. A complete rising-edge cycle is needed; insufficient cycles are unavailable. Absent pairs are omitted. This is a differential measurement, not an individual-channel amplitude or receiver logic level.'),
    h('Calculation'),
    code('Vdiff = Vpositive - Vnegative\nVpp(cycle) = maximum(Vdiff) - minimum(Vdiff)\nMean Vpp = average of complete-cycle Vpp values'),
    p('For A/-A, Vnegative is the captured voltage on the -A wire, not a sign change applied to A. B/-B and Z/-Z use the same rule. The raw differential waveform is used; common-mode voltage cancels.'),
    h('Example'),
    p('If a differential cycle reaches +3.898 V and -4.000 V, its peak-to-peak swing is 7.898 V. This can be approximately twice the swing of either individual wire. The mean is taken across complete cycles, not from one global maximum and minimum across the file.'),
    h('Scope and interpretation'),
    p('The value follows Whole recording or Visible span measurement scope. Partial cycles are excluded. RPM engineering-time calibration does not scale volts. Recorded peaks, noise and overshoot are included; this is not a settled plateau-level estimate. Z/-Z duty remains not applicable even when its voltage swing is available.'),
    p('BIN voltage scaling comes from BXM metadata. Resampled or aligned data uses the retained samples described by its warning. Keep the original captures when investigating amplitude or edge anomalies.'),
])

chapter('csv-single', 'Separate single-channel CSV files', 'You can load just A and B, or any other available channels.', [
 h('Select and assign'), p('Choose <b>CSV single channel</b> in Load recordings. The optional rows are A, -A, B, -B, Z and -Z in that order. Browse each available channel and leave the others blank; A and B alone are valid. The row you choose tells the viewer which channel it is; the signal heading in the file does not override it.'),
 h('Input and timing'), p('Each file contains one signal, optionally with a time column and header. Explicit time units are normalised before comparison. Physical-time inputs must describe the same sample grid and sample count. Sample-index inputs must match by row. These files are not automatically stretched to fit; incompatible timelines show a red error.'),
 h('Analyse and save'), p('Load the selected channels together. The circular loading dialog stays visible through import and analysis. Available channels feed the ordinary measurements, receiver reconstruction, replay and reports. Missing pairs or references give unavailable results rather than fabricated measurements.'), p('Download CSV writes the combined recording in the normal six-channel layout. Unavailable channel columns are zero-filled in this combined export; retain the source files and record which channels were actually captured. The combined input limit is 10 GiB.'),
])
chapter('csv-unlabelled', 'CSV column interpretation', 'What happens when the headings are missing or wrong.', [
 h('Named and sparse inputs'), p('Valid named layouts preserve their identity: for example Time_s,A,B loads A and B, without treating B as -A. Supported positive/negative aliases are described in Prepare recording files.'),
 h('Unlabelled or wrongly labelled columns'), p('If headings are blank, generic, partially recognised or unsuitable, the ordinary CSV loader assumes the first column is time and assigns subsequent columns as <b>A, -A, B, -B, Z, -Z</b>. At most six signal columns are supported. An amber notice describes the assumption. Headerless numeric files keep their first sample.'),
 p('In positional fallback, inferred time is treated as seconds unless explicit time units identify another scale. Explicit sample-index inputs remain sample-index data. Check the time range, sample rate and channel order before interpreting RPM, phase or missing pulses. Column labels alone cannot establish that probes were connected correctly.'),
 h('Correct the saved headings'), p('Use '+link('csv-editor','Rename CSV columns')+' to create a clearly labelled copy. Renaming is an export operation: it does not remap or reanalyse the already loaded recording. Reopen the saved copy if you want its headings used for a new import.'),
])
chapter('csv-editor', 'Rename and save CSV columns', 'Look just below Load recordings after opening a file.', [
 h('Edit the headings'), p('Each original CSV column has an editable name in the <b>Rename CSV columns</b> box. Enter non-empty, unique names, up to 128 characters, without control characters. Save becomes available when the names change. Unicode names and CSV quoting are supported.'),
 h('Save a new file'), p('Press the save button to download the renamed CSV. The original recording is unchanged. For an original CSV, the data rows, delimiter, order and time units are preserved; only the header changes. Headerless files gain a header and keep the first numeric sample. Existing byte-order marks are retained.'),
 h('Combined recordings'), p('For BIN, merged and separate-channel recordings, the editor exports the canonical time plus six-channel layout. Absent channels are zero-filled. This gives you one combined recording, rather than a copy of each original file.'),
 p('Changing a heading does not change the channels already being analysed. Use clear names such as Time_s if you plan to reopen the saved file. Keep any timing-refined filename suffix, and hang on to the original captures and alignment notes.'),
])
chapter('phase-consistency', 'A/B phase consistency', 'Look for a cycle whose A/B phase differs from the rest.', [
 h('What is measured'), p('Every unambiguous complete A cycle containing one B rising edge contributes to a robust circular phase baseline. At least eight paired cycles are needed. The analysis compares each measured phase with the typical phase; low coverage and insufficient data are reported.'),
 h('Tolerance and visibility'), p('The tolerance slider ranges from <b>5% to 20%</b>, default <b>10%</b>. This is relative to the magnitude of the typical signed phase: a 90-degree baseline gives a 9-degree tolerance at 10%. An additional sampling-resolution guard of three samples and a small angular floor prevent unresolved changes being flagged. Changes apply when the slider is released, without a loading modal.'),
 p('The hide-faults checkbox removes these phase highlights and skips them in ordinary error navigation while retaining counts and metrics. Dedicated phase zoom is disabled while hidden. Otherwise Zoom to phase error shows roughly 16 cycles with measured phase, typical phase and deviation.'),
 h('Receiver versus captured signals'), p('The RS-422 diagram uses faults calculated after receiver reconstruction. Raw-wire phase candidates that disappear at the receiver are not drawn there. A real receiver-output phase change can remain visible even when quadrature transitions remain valid.'),
 p('Electrical-cycle degrees are not antenna bearing degrees. A phase warning does not itself prove lost counts: transition order, receiver thresholds, decoder behaviour and encoder PPR determine its practical effect.'),
])
chapter('health-overview', 'Signal health and trends', 'Use these checks to spot changes and jump to the relevant pulses.', [
 h('Use the controls'), p('Expand the relevant health check to see results. Event buttons select an available anomaly, show local feedback and scroll to its waveform. Buttons are disabled with an explanation when the required event or channel is absent. Clicking a trend selects its source time and zooms to a short waveform span, up to about 16 source cycles.'),
 h('Trends use the full file'), p('Up to 192 time bins retain means and extrema from all valid observations. The plotted trend is a summary, not a restriction to the browser preview. A channel selector changes the pulse-width/noise channel. Interactive controls do not open loading dialogs.'),
 h('Phase drift'), p('Time-binned A/B phase shows mean and range, slope and net change between the first and last binned means. Read this with phase consistency: slow drift and a single exceptional cycle are different observations.'),
 h('Z stability'), p('Validated Z events are located within the local A/B electrical cycle. Circular mean, RMS and range describe index-position stability. This has no automatic Z tolerance and is not a direct shaft-angle error measurement.'),
 h('Rotation and speed'), p('Quadrature checks identify valid Gray-code transitions, ambiguous both-bit changes and direction changes. Limited sampling can make a transition ambiguous. Physical Z-to-Z intervals provide measured RPM range, peak-to-peak speed variation and revolution-average acceleration in RPM/s. This requires at least two validated Z events and assumes one index per revolution; entered RPM does not overwrite measured health speed.'),
])
chapter('health-electrical', 'Electrical and pulse-width health', 'Look at voltage balance, pulse widths and noise.', [
 h('Differential pair balance'), p('When a complete pair is available, the check reports common-mode mean and range, differential low/high means, signed level imbalance and threshold complement agreement. Nearest opposite edges within a quarter mean cycle provide skew estimates and unmatched-edge counts. These measurements do not certify RS-422 compliance.'),
 h('Pulse-width trends'), p('Select a channel to inspect high- and low-width trends for complete cycles. Mean and extrema reveal changes across the recording. Z/-Z widths are available, but Z/-Z duty cycle is deliberately not calculated or tolerance-tested.'),
 h('Noise and short pulses'), p('Unsmoothed threshold plateaus supply low/high RMS estimates. They can include drift and transition ramps; this is not a band-limited noise specification. Compare raw crossings with analysed crossings to see activity rejected by processing.'),
 p('Short-pulse candidates are complete interior runs shorter than 10% of the mean analysed high/low width. Endpoint runs are excluded. These are raw-domain candidates: they do not automatically appear as receiver-output faults.'),
 h('Waveform presentation'), p('Raw voltage plots include headroom around visible extrema so positive noise and overshoot are visible. Smooth edges deselects and disables Non-thresholded raw plot; deselecting smoothing re-enables that option. Receiver error overlays use receiver-domain checks, independently of raw-wire candidate density.'),
])
chapter('bearing-test', 'Bearing-error test and comedy', 'Try a known bearing error, then watch the next Z reset.', [
 h('Generate a 1-5 degree bearing error'), p('The header button makes a three-revolution recording with 8192 pulses per revolution at 60 RPM. It removes complete A and B cycles in the middle revolution, preserving complementary pairs and Z. A fresh random seed selects 23-113 missing cycles, giving an actual count-derived error between about 1.01 and 4.97 degrees.'),
 p('The generator enables the 8192-PPR reference. Its amber notice reports the selected angle, missing cycles, time window, next Z reset and seed. The profile information survives reanalysis. Other test generators remain available: clean, missing pulses and Gaussian jitter.'),
 h('What replay demonstrates'), p('The lost complete cycles create a consistent count-derived angular lag for x1, x2 and x4 decoding until the next Z reset. Z remains the zero reference. Use the position simulator and green replay marker to inspect the affected interval.'),
 p('Use this test to see how lost counts can turn into a bearing lag. Your radar may behave differently: it might interpolate, reject bad counts or apply its own corrections. Also remember that phase within one electrical cycle is different from the antenna bearing.'),
 h('Emergency comedy button'), p('Need a break? The button below the bearing-error generator opens an engineering joke. Use Back to counting or Escape to close it. Your recording and settings stay as they were.'),
])


chapter('virtual-env', 'Your Python virtual environment', 'Create it once; use it every time you start the viewer.', [
 p('A virtual environment is a private copy of Python for this app. Its packages live in the .venv folder beside server.py, so installing NumPy here does not change your other Python programs. Start in the extracted app folder.'),
 h('Windows: create and check it'),
 code('py -3.12 -m venv .venv\n& .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n& .\\.venv\\Scripts\\python.exe -c "import numpy, reportlab; print(\'Ready\')"'),
 p('Use the Python version you installed if it differs from 3.12. Once you see Ready, double-click Start-Viewer.bat to run the app. If a command says NumPy is missing, make sure you used the .venv Python shown above, rather than a different system Python.'),
 h('Linux: create and check it'),
 code('python3 -m venv .venv\n.venv/bin/python -m pip install -r requirements.txt\n.venv/bin/python -c "import numpy, reportlab; print(\'Ready\')"'),
 p('If venv or ensurepip is missing, install the matching venv package first. On Debian/Ubuntu it is usually python3-venv. Once setup finishes, run sh start-viewer.sh.'),
 h('Do I need to activate it?'),
 p('No. The commands above use the environment directly. If you prefer to activate it, use the command for your system below, then run python server.py. Type deactivate when finished. Closing the terminal also ends activation.'),
 code('# Windows PowerShell\n.\\.venv\\Scripts\\Activate.ps1\n# Linux\nsource .venv/bin/activate'),
 p('If PowerShell blocks activation, use the full .venv Python path instead; you do not need to change the execution policy. Do not copy .venv to another machine or commit it to Git. Create a fresh one there and reinstall requirements.txt.'),
], 'INSTALL')

# Insert added topics logically while keeping installation as the last five chapters.
by_key = {c['key']:c for c in CHAPTERS}
new_order=[]
for c in CHAPTERS:
    if c['key'] in ('bin-loading','bin-timebase','bin-quadrature','differential-vpp','csv-single','csv-unlabelled','csv-editor','phase-consistency','health-overview','health-electrical','bearing-test','virtual-env'): continue
    if c['key']=='windows-python': new_order.append(by_key['virtual-env'])
    new_order.append(c)
    if c['key']=='formats': new_order.extend(by_key[k] for k in ('csv-single','csv-unlabelled','csv-editor','bin-loading','bin-timebase','bin-quadrature'))
    if c['key']=='measurements': new_order.extend(by_key[k] for k in ('differential-vpp','phase-consistency','health-overview','health-electrical'))
    if c['key']=='generator': new_order.append(by_key['bearing-test'])
CHAPTERS[:] = new_order

INDEX = {
    'Activate environment': ['virtual-env'], 'Virtual environment': ['virtual-env'], 'NumPy missing': ['virtual-env'],
    'CSV single channel': ['csv-single'], 'Unlabelled columns': ['csv-unlabelled'], 'Rename CSV columns': ['csv-editor'], 'Phase consistency': ['phase-consistency'], 'Phase tolerance slider': ['phase-consistency'], 'Hide phase faults': ['phase-consistency'], 'Signal health': ['health-overview','health-electrical'], 'Common-mode voltage': ['health-electrical'], 'Short pulses': ['health-electrical'], 'Bearing-error generator': ['bearing-test'], 'Comedy button': ['bearing-test'],
    'Amber warning': ['bin-timebase','bin-quadrature'], 'Assumed 90-degree alignment': ['bin-quadrature'], 'BIN files': ['bin-loading','bin-timebase'], 'BXM metadata': ['bin-loading'], 'Differential peak-to-peak': ['differential-vpp'], 'Nearest-sample resampling': ['bin-timebase'], 'Time-base adjustment': ['bin-timebase'], 'Voltage swing': ['differential-vpp'], 'CSV/BIN selector': ['bin-loading','workspace'], 'Advanced settings': ['workspace','smoothing'], 'No-Z replay': ['replay'], 'Drag selection box': ['waveforms'],
    'Alignment choice dialog': ['alignment-choice'], 'Refine B timing': ['alignment-choice'],
    'A/B alignment check': ['alignment', 'merge'], 'A/B phase': ['measurements', 'alignment'], 'Analog rise/fall': ['smoothing', 'measurements'],
    'Auto threshold': ['thresholds', 'large-files'], 'Azimuth': ['replay'], 'Bookmarks and search': ['exports'], 'Browse': ['quick-start', 'formats'],
    'Bunches of missing pulses': ['generator'], 'Cache': ['large-files', 'workspace'], 'Cache size': ['large-files', 'workspace'], 'Delete cache': ['large-files', 'workspace'], 'Calibration': ['time', 'replay'], 'Cancel': ['workspace'],
    'Capture A / capture B': ['merge'], 'Capture C (optional)': ['alignment'], 'Channel aliases': ['formats'], 'Channel selection': ['waveforms'],
    'Clean test data': ['generator'], 'Complete revolutions': ['missing', 'glossary'], 'Composite recordings': ['merge', 'alignment'],
    'CSV download': ['exports'], 'CSV headings': ['formats'], 'Cursors': ['waveforms', 'replay'], 'Default port': ['quick-start', 'windows-app'],
    'Delimiter': ['formats'], 'Dependencies': ['windows-app', 'linux-app'], 'Differential channels': ['thresholds', 'generator'],
    'Direction': ['replay', 'measurements'], 'Disk space': ['large-files'], 'Discrepancies': ['missing'], 'Downloads': ['exports'],
    'Duty cycle': ['measurements'], 'Edge filter': ['smoothing'], 'Encoder serial number': ['exports'], 'Errors': ['waveforms', 'troubleshooting'],
    'Expected PPR': ['missing', 'time'], 'Exports': ['exports'], 'Extra pulses': ['missing'], 'Eye diagrams': ['eyes'],
    'Failed fetch': ['troubleshooting'], 'File limits': ['formats', 'large-files'], 'Fit all': ['waveforms'], 'Frequency': ['measurements', 'time'],
    'Full recording': ['workspace', 'waveforms'], 'Follow replay': ['replay', 'waveforms'], 'Gaussian jitter': ['generator'], 'Generated data': ['generator'], 'Glossary': ['glossary'],
    'High / low width': ['measurements'], 'Histogram overflow': ['eyes'], 'Hysteresis': ['thresholds'], 'Index pulses': ['eyes', 'missing'],
    'Inferred reference': ['missing'], 'Installation, Linux': ['linux-python', 'linux-app'], 'Installation, Windows': ['windows-python', 'windows-app'],
    'Jitter': ['eyes', 'generator'], 'Large files': ['large-files'], 'Linux': ['linux-python', 'linux-app'], 'Loading dialogs': ['workspace'],
    'Local file path': ['quick-start'], 'Loop playback': ['replay'], 'Low / high threshold': ['thresholds'], 'Measurement scope': ['workspace', 'measurements'],
    'Merge': ['merge', 'alignment'], 'Missing pulses': ['missing', 'generator'], 'Moving average': ['smoothing'], 'Native file picker': ['formats', 'troubleshooting'],
    'Negative channels': ['thresholds', 'glossary'], 'Navigation box': ['waveforms'], 'Recording navigator': ['waveforms'], 'Not applicable (Z duty)': ['measurements'], 'Offline installation': ['offline-install'],
    'OOS cycles': ['measurements', 'eyes'], 'Pan': ['waveforms'], 'PDF reports': ['exports'], 'Period': ['measurements'], 'Phase': ['alignment', 'measurements'],
    'pip': ['windows-app', 'linux-app', 'offline-install'], 'Port conflict': ['windows-app', 'troubleshooting'], 'Position Simulator': ['replay'],
    'PPR': ['time', 'missing'], 'Progress circle': ['workspace'], 'Pulse counter': ['replay'], 'Pulse stepping': ['replay'],
    'Python installation': ['windows-python', 'linux-python'], 'Raw plot': ['waveforms', 'smoothing'], 'Background reanalysis': ['workspace'], 'Receiver logic': ['thresholds'],
    'Reference capture': ['alignment', 'alignment-choice'], 'Reference count': ['missing'], 'Replay speed': ['replay'], 'RF64': ['formats'], 'Rise/fall estimator': ['smoothing'],
    'RPM': ['time', 'replay'], 'RS422': ['thresholds', 'glossary'], 'Sample index': ['formats', 'time'], 'Sample rate': ['formats', 'alignment'],
    'Searchable PDF': ['exports'], 'Seed': ['generator'], 'Serial number': ['exports'], 'Single-ended fallback': ['thresholds', 'replay'],
    'Smooth edges': ['smoothing'], 'Stop the server': ['windows-app', 'linux-app'], 'Synchronization': ['merge', 'alignment'],
    'Test data': ['generator'], 'Third capture': ['alignment'], 'Thresholds': ['thresholds'], 'Time column': ['formats', 'time'],
    'Time scale': ['time', 'alignment'], 'Tk / tkinter': ['windows-python', 'linux-python'], 'Tolerance': ['measurements', 'alignment'],
    'Troubleshooting': ['troubleshooting'], 'TSV': ['formats'], 'Updates': ['offline-install'], 'venv': ['windows-app', 'linux-app'],
    'Visible span': ['workspace', 'measurements'], 'Voltage difference': ['thresholds'], 'WAV': ['formats'], 'Wheelhouse': ['offline-install'],
    'Whole-file analysis': ['workspace', 'large-files'], 'Windows': ['windows-python', 'windows-app'], 'Z reset': ['replay'], 'Z synchronization': ['merge'],
    'Zoom to error': ['waveforms'],
}


def flow(block):
    kind, *args = block
    if kind in ('p', 'h'):
        return Paragraph(args[0], SUB if kind == 'h' else BODY)
    if kind == 'code':
        return Paragraph(escape(args[0]).replace(' ', '&nbsp;').replace('\n', '<br/>'), CODE)
    rows, widths = args
    values = [[Paragraph(escape(str(cell)), CELL) for cell in row] for row in rows]
    t = Table(values, colWidths=widths or [180, 322], hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),colors.white),('ROWBACKGROUNDS',(0,1),(-1,-1),[LIGHT,colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),1,BLUE)]))
    # Paragraph colours are explicit; set header paragraphs to white separately.
    for j, value in enumerate(rows[0]):
        values[0][j] = Paragraph('<font color="white"><b>'+escape(str(value))+'</b></font>', CELL)
    t = Table(values, colWidths=widths or [180, 322], hAlign='LEFT', style=t._cellStyles and TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[LIGHT,colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    return t


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    operation = CHAPTERS[:-6]
    install = CHAPTERS[-6:]
    index_keys = sorted(INDEX, key=str.casefold)
    # Two columns per index page keep navigation compact and readable.
    index_pages=[]
    for group in range(3):
        terms=index_keys[len(index_keys)*group//3:len(index_keys)*(group+1)//3]
        index_pages.append(dict(key='index-'+str(group+1),title='Alphabetical index '+terms[0][0].upper()+'-'+terms[-1][0].upper(),terms=terms))
    topics = operation + index_pages + install
    contents = [dict(key='contents' if n == 0 else 'contents-'+str(n+1), title='Contents' if n == 0 else 'Contents (continued)', targets=topics[n*23:(n+1)*23]) for n in range((len(topics)+22)//23)]
    pages = [dict(key='cover', title='User guide')] + contents + topics
    numbers = {page['key']: i + 1 for i, page in enumerate(pages)}
    c = canvas.Canvas(str(OUT), pagesize=A4, pageCompression=1)
    c.setTitle('Pulse Scope IO Offline - Comprehensive User Guide')
    c.setAuthor('Pulse Scope IO')
    c.setSubject('Searchable operator guide, linked index and Windows/Linux installation')
    c.setKeywords('encoder pulse viewer offline A/B Z merge alignment Python Windows Linux user guide')
    c.showOutline()
    logo = ImageReader(str(ROOT/'static/pulse-scope-io-logo.png'))
    for i, page in enumerate(pages):
        number = i + 1
        c.bookmarkPage(page['key'])
        c.addOutlineEntry(page['title'], page['key'], 0, False)
        if page['key'] == 'cover':
            c.drawImage(logo, 44, H-175, width=300, height=100, preserveAspectRatio=True, mask='auto')
            c.setFillColor(NAVY); c.rect(0, 140, W, 460, fill=1, stroke=0)
            c.setFillColor(colors.HexColor('#73d8e7')); c.setFont('GuideBold',11); c.drawString(44, 558,'OFFLINE / PYTHON EDITION')
            c.setFillColor(colors.white); c.setFont('GuideBold',31)
            c.drawString(44,505,'Pulse Scope IO')
            c.setFont('Guide',24); c.drawString(44,463,'Comprehensive user guide')
            c.setFillColor(colors.HexColor('#c9d9e9')); c.setFont('Guide',11)
            for y, text in [(418,'CSV/BIN loading. Full-file analysis. Six-channel merging.'),(398,'Time-base adjustment. A/B phase checks. Pulse-driven replay.'),(345,'Searchable text | Linked contents | Bookmarks | Alphabetical index')]:
                c.drawString(44,y,text)
            for row, col in enumerate(['#4dd6e8','#50d697','#ff6178']):
                c.setStrokeColor(colors.HexColor(col)); c.setLineWidth(1.7)
                y=290-row*26; path=c.beginPath(); path.moveTo(44,y)
                for k in range(16):
                    x=44+k*31; path.lineTo(x,y); path.lineTo(x,y+12 if k%2==0 else y); path.lineTo(x+15,y+12 if k%2==0 else y)
                c.drawPath(path)
            c.setFillColor(NAVY);c.setFont('GuideBold',12);c.drawString(44,100,'Pulse Scope IO Where Every Pulse Counts!')
            c.setFont('Guide',9);c.setFillColor(GREY);c.drawString(44,76,'Edition 1.6 | 24 October 2026 | Pulse Scope IO')
            c.linkRect('', 'contents', (40,60,W-40,125), relative=0, thickness=0)
            c.setFont('Guide',8); c.setFillColor(BLUE); c.drawString(44,25,'L33T Group UK'); c.linkURL('https://www.L33T.uk',(44,21,105,36),relative=0,thickness=0)
            c.showPage(); continue
        c.setFillColor(NAVY);c.setFont('GuideBold',8.6);c.drawString(44,H-34,'PULSE SCOPE IO')
        c.setFillColor(GREY);c.setFont('Guide',8);c.drawRightString(W-44,H-34,'PULSE SCOPE IO / USER GUIDE')
        c.setStrokeColor(BLUE);c.setLineWidth(2);c.line(44,H-47,W-44,H-47)
        c.setFillColor(BLUE);c.setFont('GuideBold',8);c.drawString(44,H-68,page.get('category','REFERENCE')+'  /  '+f'{number:02d}')
        c.setFillColor(NAVY);c.setFont('GuideBold',21);c.drawString(44,H-95,page['title'])
        ytop=H-113
        if 'strap' in page:
            strap = Paragraph(page['strap'], SMALL)
            strap.wrapOn(c,W-88,30)
            strap.drawOn(c,44,H-116)
            ytop=H-135
        c.setStrokeColor(colors.HexColor('#cfdae5'));c.setLineWidth(.5);c.line(44,40,W-44,40)
        c.setFont('Guide',7.6);c.setFillColor(GREY);c.drawString(44,25,'Pulse Scope IO | Offline analysis')
        c.setFillColor(BLUE);c.drawString(44,13,'L33T Group UK');c.linkURL('https://www.L33T.uk',(44,9,105,22),relative=0,thickness=0)
        c.setFillColor(BLUE);c.drawCentredString(W/2,25,'Contents');c.linkRect('', 'contents', (W/2-25,20,W/2+25,35), thickness=0)
        c.setFillColor(GREY);c.drawRightString(W-44,25,f'{number} / {len(pages)}')
        if page['key'].startswith('contents'):
            blocks=[Paragraph('Choose a topic below or use the PDF bookmarks. Page numbers and topic labels are clickable. Installation is at the end; the alphabetical index precedes it.',BODY),Spacer(1,7)]
            for target in page['targets']:
                num=numbers[target['key']]
                text=f'<a href="#{target["key"]}" color="#142842">{target["title"]}</a>'
                t=Table([[Paragraph(text,BODY),Paragraph(f'<a href="#{target["key"]}" color="#086f8c">{num:02d}</a>',BODY)]],colWidths=[464,38])
                t.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),1),('LINEBELOW',(0,0),(-1,-1),.25,colors.HexColor('#dce4ee'))]))
                blocks.append(t)
        elif page['key'].startswith('index-'):
            wanted=page['terms']
            entries=[]
            for term in wanted:
                refs=', '.join(f'<a href="#{key}" color="#086f8c">{numbers[key]}</a>' for key in INDEX[term])
                entries.append(Paragraph(escape(term)+'  '+refs,SMALL))
            # Distribute entries to two independent frames, never split a term.
            split=(len(entries)+1)//2
            for col, part in enumerate((entries[:split],entries[split:])):
                Frame(44+col*258,54,244,ytop-58,leftPadding=0,rightPadding=3,topPadding=0,bottomPadding=0).addFromList(part,c)
                if part: raise RuntimeError('Index overflow: '+page['key'])
            blocks=[]
        else:
            blocks=[]
            for block in page['blocks']:
                blocks.append(flow(block))
                if block[0]=='table':blocks.append(Spacer(1,9))
        if blocks:
            Frame(44,54,W-88,ytop-58,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0).addFromList(blocks,c)
            if blocks: raise RuntimeError('Page overflow: '+page['key']+' ('+str(len(blocks))+' blocks left)')
        c.showPage()
    c.save()
    (ROOT/'audit/guide-navigation.json').write_text(json.dumps(dict(pages=numbers,index=INDEX),indent=2),encoding='utf8')
    print(f'Created {len(pages)} pages: {OUT}')


if __name__ == '__main__': build()
