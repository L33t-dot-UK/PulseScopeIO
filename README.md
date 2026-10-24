# Pulse Scope IO

![Pulse Scope IO logo](pulse-scope-io-logo.svg)

**This is a fully featured version that is quicker and more powerful than the online version. This version can handle files of up to 10 GB in size and supports BIN files. Full installation instructions for Windows and Linux are below. Download the code, create the venv and run the application.**

A Python-backed, browser-based tool for examining incremental encoder captures: A, −A, B, −B, Z and −Z. Import recordings, analyse the complete file, inspect reconstructed RS-422 receiver outputs and replay count-derived azimuth. Processing runs locally.

[Features](#features) · [Requirements](#requirements) · [Windows](#windows-installation) · [Linux](#linux-installation) · [Inputs](#recording-inputs) · [Documentation](#documentation) · [Troubleshooting](#troubleshooting)

## Features

- Whole-file analysis with disk-backed processing and bounded browser plots; supported inputs up to 10 GiB, subject to the limits below.
- Multi-channel CSV, separate single-channel CSV, and individual BIN/BXM channel recordings. Unavailable channels can be omitted.
- Two four-channel captures merged using shared Z pulses; an optional third A/B capture checks alignment or, with an explicit choice, refines B timing.
- Threshold and hysteresis reconstruction, optional smoothing and edge filtering, raw voltage and receiver-output diagrams.
- Pulse counts by revolution, missing/extra pulses, widths, A/B duty cycle, frequency, phase, differential peak-to-peak voltage, eye diagrams and jitter histograms.
- Adjustable A/B phase consistency checks and signal health trends: drift, pair balance, index stability, quadrature transitions, speed, pulse widths and noise candidates.
- Drag-to-zoom, waveform overview navigator, error navigation and zoom that follows playback.
- Looping position replay at 0.1×–1× speed, using available pulse channels and Z resets.
- Clean, missing-pulse, jitter and random 1–5° bearing-error test recordings, plus an engineering comedy button.
- CSV column-name editor, CSV downloads, engineering-unit PDF reports and a linked, searchable user guide.
- Cache size display and deletion control.

## Requirements

| Item | Requirement |
| --- | --- |
| Python | 64-bit Python 3.12 or newer; 3.12 is the development baseline |
| Runtime packages | `numpy>=2.0,<3`, `reportlab>=4,<5`; see [requirements.txt](requirements.txt) |
| Browser | A current desktop browser with JavaScript and Canvas enabled |
| Windows picker | Native Windows dialog via Python `ctypes`; Tkinter is not required |
| Linux picker | Tkinter and a graphical desktop session |
| Storage | Writable application/cache directory with space for decoded data, indexes and exports |
| Development PDF checks | `pypdf>=5,<7`; see [requirements-dev.txt](requirements-dev.txt) |

Node.js, npm and an online account are not required to run the application. Packages normally need internet access for first installation; see [offline installation](#offline-installation).

## Get the application

Download the repository using GitHub **Code → Download ZIP**, or clone its actual repository URL. Extract it into a writable local folder, keeping `server.py`, `static/` and the other project files together. Do not run from inside a ZIP archive.

A supplied `EncoderPulseViewer-Offline.zip` distribution can be extracted in the same way. Avoid placing large recordings or generated caches in Git.

## Windows installation

1. Install an official **64-bit Python 3.12 or newer** from [Python.org](https://www.python.org/downloads/). Include pip and the launcher. If using the Python Install Manager, install the intended runtime; with a traditional installer, select the relevant installer options. Open a new PowerShell window afterward.
2. Extract the project, for example into `C:\EncoderPulseViewer`.
3. Create a virtual environment and install dependencies:

```powershell
Set-Location -LiteralPath 'C:\EncoderPulseViewer'
py -3.12 --version
py -3.12 -c "import struct; print(struct.calcsize('P') * 8)"
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe server.py
```

The architecture check must print `64`. If you deliberately installed another supported version, replace `-3.12` with that version. If `py` is unavailable, use the full path to the installed Python executable. Virtual-environment activation is unnecessary when using these explicit paths.

The browser normally opens automatically at **http://127.0.0.1:8765/**. Keep the server console open. For later runs, double-click **Start-Viewer.bat**, which uses the local `.venv` when present.

`Setup.ps1` is an alternative setup helper, using the runtime selected by `py -3`. The explicit commands above give control over the Python version and avoid needing to change PowerShell script-execution policy.

Official reference: [Using Python on Windows](https://docs.python.org/3.12/using/windows.html).

## Linux installation

Install a 64-bit Python 3.12+ interpreter, venv, pip and Tk using your distribution packages. Check the version: older distribution releases may need an additional supported Python package or an OS upgrade.

Ubuntu/Debian, where these packages provide a suitable version:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-tk
python3 --version
```

Fedora:

```sh
sudo dnf install python3 python3-pip python3-tkinter
python3 --version
```

Extract the project, then run:

```sh
cd /path/to/EncoderPulseViewer
python3 -c "import struct; print(struct.calcsize('P') * 8)"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

The architecture check must print `64`. To use a specific installed interpreter, substitute `python3.12` or its path when creating `.venv`. Install matching distribution venv/Tk packages if required. Do not install application packages into system Python using `sudo pip`.

For subsequent runs:

```sh
sh start-viewer.sh
```

Browse requires a graphical desktop and working Tkinter. Test it with `python3 -m tkinter`; close the test window afterward. A headless service can run without a picker, but the supplied configuration is local-only and does not configure remote access.

Official references: [Python on Unix](https://docs.python.org/3.12/using/unix.html), [virtual environments](https://docs.python.org/3.12/library/venv.html).

## Running and stopping

The server binds to `127.0.0.1`. To use another port or suppress automatic browser opening:

```sh
# Use your virtual environment's Python executable:
python server.py --port 8766 --no-browser
```

Open the printed address manually. Stop the service with **Ctrl+C** in its terminal. After restarting, refresh the browser so its local-session token is renewed; reload the recording as needed.

## Your virtual environment

A virtual environment is a small Python installation kept in the project's `.venv` folder. It keeps this app's NumPy and ReportLab packages separate from other Python programs. Create it once after extracting the app, then use it whenever you run the viewer.

### Windows (PowerShell)

Open PowerShell in the folder containing `server.py` and run:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -c "import numpy, reportlab; print('Ready to run')"
& .\.venv\Scripts\python.exe server.py
```

Use the version you installed if it differs from 3.12. You should see `Ready to run`. For everyday use, double-click `Start-Viewer.bat`; it uses this environment. If the environment is missing, the launcher explains how to create it. If packages are missing, it attempts to install them from `requirements.txt` (or a supplied `wheelhouse`).

### Linux (terminal)

In the app folder, run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import numpy, reportlab; print('Ready to run')"
.venv/bin/python server.py
```

If Python says `venv` or `ensurepip` is missing, install your distribution's matching venv package first; on Debian/Ubuntu this is usually `python3-venv`.

### Activation is optional

The commands above use the environment directly, so activation is not needed. If you prefer shorter commands, activate it first:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
python server.py
```

```sh
# Linux
source .venv/bin/activate
python server.py
```

If PowerShell blocks activation, use the explicit `.venv\Scripts\python.exe` commands instead; there is no need to change the execution policy. Type `deactivate` when finished with an activated environment. Closing the terminal also ends activation; it does not delete `.venv`.

Do not commit `.venv` to Git or copy it to another machine. Recreate it there and reinstall `requirements.txt`. If NumPy cannot be found, first check which Python you are running:

```powershell
# Windows
& .\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

```sh
# Linux
.venv/bin/python -c "import sys; print(sys.executable)"
```

The printed path should point inside this app's `.venv` folder. Install missing packages using that same executable with `-m pip install -r requirements.txt`.

## Recording inputs

Select the recording format in **Load recordings**. Browse selects local files for the local Python service; recordings are not uploaded to a cloud service.

### Multi-channel CSV

A named layout can contain any available subset of A, −A, B, −B, Z and −Z. Explicit time units such as `Time_s`, `Time_ms`, `Time_us` and `Time_ns` are supported. Comma, semicolon and tab delimiters are supported.

```csv
Time_s,A,-A,B,-B,Z,-Z
0.000000,0,5,0,5,0,5
0.000010,5,0,0,5,5,0
0.000020,5,0,5,0,0,5
```

This tiny example demonstrates syntax, not a useful analysis capture. Numeric values must be finite; rows must have consistent field counts; selected time must increase strictly.

Recognised sparse labels preserve identity: `Time_s,A,B` means A and B. For unrecognised, blank or unsuitable headings, positional fallback assumes **time, A, −A, B, −B, Z, −Z**, with an amber notice. Headerless numeric files keep their first sample. Verify the assumptions before interpreting results. Positional inferred time uses seconds unless explicit units say otherwise; sample-index data requires appropriate calibration.

### CSV single channel

Choose **CSV single channel** to select individual files in the A, −A, B, −B, Z, −Z rows. Leave absent channels blank. Each file contains one signal with optional time/header. The row determines channel identity.

Selected files must have matching sample counts and compatible timestamps after unit conversion, or matching sample-index rows. This mode does not stretch mismatched timelines; rejected inputs show a red error. Press the load button after choosing the channels.

### BIN + BXM

Select up to six raw channel files. Each `.BIN` needs matching, same-stem `.BXM` XML metadata alongside it. The supported format is single-channel UINT16, either byte order, with record length, timing and voltage scaling supplied by metadata. Other binary layouts are rejected rather than guessed.

Different valid time bases are resampled onto A's grid, or the first available channel's grid, with common-duration trimming and an **amber warning**. This can change sampling resolution and narrow details. Rebasing capture origins does not prove physical alignment.

The optional assumed 90° A/B setting shifts B/−B together by a constant whole-sample offset after any required resampling. It preserves complete retained pulse widths in the shift step; it does not establish the true angle between independent captures. Incompatible rotation speeds are rejected.

### Merge two four-channel captures

- Capture A: A, −A, Z, −Z.
- Capture B: B, −B, Z, −Z.
- Optional alignment capture: A, −A, B, −B, with an independently supported sample rate/duration.

Shared Z events align the primary captures. The optional third capture opens a choice between checking alignment and actively refining B timing. Refinement changes the resulting B waveform timing and is labelled in exports; retain original captures. Downloaded CSV contains the merged six channels.

Supported measurement WAV/RF64 inputs are also available through compatible recording/merge paths. Ordinary six-channel WAV uses A, −A, B, −B, Z, −Z order. This is not an audio player; there is no WAV export feature.

## Reading the results

Set thresholds for your captured voltage levels, confirm time interpretation, and enter the known PPR reference when checking missing pulses. Inferred PPR can hide uniform losses. Entered RPM calibrates engineering time where applicable; clear it when using physical capture timing for measured speed.

The default measurement scope is the whole recording. Visible-span measurements can be selected; PDF reports use full-file results. Zooming does not restrict the whole-file fault search. Error navigation zooms to approximately 16 cycles and identifies the channel and fault.

A/B phase consistency uses a robust circular baseline and a **5–20% tolerance**, default **10%**. The percentage is relative to typical phase magnitude, with a sampling-resolution guard. The hide checkbox suppresses these highlights/navigation while retaining metrics. Electrical-cycle phase degrees are not antenna bearing degrees, and a phase variation does not necessarily imply a lost decoder count.

Receiver-diagram errors are evaluated after receiver reconstruction. Raw-wire candidates removed by the receiver are not drawn as receiver errors. A genuine receiver-output phase variation may still be shown even when quadrature transition order remains valid.

**Signal health & trends** provides phase drift, differential/common-mode balance and skew, Z stability, quadrature validity/direction, measured speed/acceleration, width trends and raw-noise/short-pulse candidates. Trend clicks and event buttons navigate to source waveform detail. These checks describe sampled data; they are not an RS-422 compliance certificate. Z/−Z duty cycle is intentionally not calculated.

Replay loops at 10–100% speed and resets at validated Z events when present. With no independent index/reference, expected position is an estimate. The bearing-error generator demonstrates a count-derived lag until the next Z reset; actual radar correction/interpolation behaviour depends on the equipment.

## Saving and documentation

**Rename CSV columns** is expanded directly below Load recordings after loading data. Names must be non-empty, unique, at most 128 characters and contain no control characters. Save downloads a new CSV; it does not alter originals or remap the current analysis. Original CSV data rows are preserved when changing the header. Combined/BIN exports use a canonical layout with zero-filled absent channels.

**Download CSV** exports the loaded/merged recording. **Export PDF** produces the analysis report with engineering units. Keep the source files and transformation warnings with derived results.

Use the app's **User guide** link for the searchable, bookmarked manual with linked contents, alphabetical index and complete installation chapters. Distribution location: `output/pdf/Encoder_Pulse_Viewer_User_Guide.pdf`; local URL: `/user-guide.pdf`.

For release downloads, use the repository’s GitHub Releases page. The repository includes only synthetic example captures and the numerical reference results needed by tests.

Generated `output/` is ignored by Git. A repository checkout may not contain a built guide: use the release distribution, or generate it with:

```sh
# Use the virtual environment interpreter:
python guide.py
```

Guide generation also requires Arial on Windows or DejaVu Sans on Linux. The source is [guide.py](guide.py).

## Large files and cache

Single CSV/WAV sources may be up to **10 GiB (10,737,418,240 bytes)**. Separate BIN channels and separate CSV channels have a combined 10 GiB input limit. CSV records/headers have a 1 MiB row limit.

Decoded storage is substantially larger than compressed or compact source data: the canonical sample store alone uses about 56 bytes per sample, plus logic, edges, faults and other indexes. Use a local SSD with ample free space. This is not a promise that a 10 GiB source needs only 10 GiB of storage or that every noisy capture processes equally quickly. Full-capacity combinations of multiple primary captures and a third alignment file have not been benchmarked together.

The browser receives reduced display data; Python still analyses the complete imported recording. The **Delete cache** button shows cache size and closes loaded recordings before clearing cached data. Preserve source files separately. Stop the service before any manual cache maintenance.

## Offline installation

Prepare wheels on a connected machine matching the target operating system, Python version and CPU architecture:

```sh
python -m pip download --only-binary=:all: -r requirements.txt -d wheelhouse
```

Transfer the project, `wheelhouse/`, and an appropriate Python installer or distribution packages. Install Python and, for Linux native browsing, Tk/desktop dependencies. Create `.venv` as above, then:

```powershell
# Windows
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

```sh
# Linux
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

Do not copy a virtual environment between operating systems or machines. OS packages and Python itself require separate offline provisioning. Once installed, recording analysis does not require an internet connection.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Browser cannot connect | Keep the server running; use its printed address and port. Try another port if occupied. |
| Invalid session / failed fetch after restart | Refresh the page and reopen the recording. |
| Browse does not open | Check the selected format. Windows uses a native dialog; Linux needs Tk and a desktop display. Read the displayed picker error. |
| Single-channel CSV rejected | Compare sample counts, timestamp units and grids. This mode does not rescale files. |
| BIN loading rejected | Verify matching BXM, supported UINT16 layout, record length and usable metadata. |
| Unexpected phase/count errors | Verify probe assignments, thresholds, time interpretation, PPR and validated Z events. Review alignment/resampling warnings. |
| Health button disabled | The required channel/event may be absent; read the local explanation. |
| Unavailable eyes, RPM or pair voltage | Include enough complete cycles and the required channels/reference events. |
| Import slow / disk full | Check local SSD space for expanded cache and exports; noise can greatly increase edge counts. |

## Development and validation

Install development dependencies into the same environment, then run the Python regression suite:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
```

Use the virtual environment's explicit Python path if it is not activated. Some desktop/platform checks may be skipped when their prerequisites are absent. Windows validation does not establish that every Linux distribution or newer Python version has been tested.

The `tests/` folder also contains optional JavaScript/browser checks with additional development prerequisites; they are not needed to operate the app. The browser smoke check uses Playwright (`npm install --no-save playwright` and `npx playwright install chromium`). Set `PYTHON` to your environment interpreter if needed; `CHROME_PATH` optionally selects an installed Chrome executable. Synthetic good/bad captures are useful for checking changes without sharing private recordings.

Main components:

| Path | Responsibility |
| --- | --- |
| `server.py` | Local HTTP service, jobs and exports |
| `engine.py`, `phase.py`, `health_analysis.py` | Core measurements and diagnostics |
| `binary_import.py`, `csv_channels.py`, `merge.py` | Import and capture combination |
| `static/` | Browser interface and plots |
| `synthetic.py` | Repeatable test signal generation |
| `report.py`, `guide.py` | PDF report and manual |
| `tests/` | Regression tests |

For bug reports, include OS/Python version, import mode, time/voltage units, channel layout, relevant settings, expected/observed result and a small synthetic reproducer where possible. Remove sensitive recording data before sharing. Follow existing patterns and include relevant regression coverage for behavioural changes.

## Updating and privacy

Stop the service before replacing program files. Preserve original captures, install the new requirements in a recreated or updated virtual environment, and reload recordings. Clear obsolete cache through the application when necessary. Review transformation warnings when comparing results across versions.

The application runs on loopback and processes recording data locally. It is not configured as a public or multi-user web service. The optional Buy Me a Coffee footer button loads its script and styling from external services when online; the button needs an internet connection. Recording analysis stays local. External documentation/credit links open external websites when selected.

## Credits and licensing

Pulse Scope IO — Where Every Pulse Counts!

Copyright © 2026 David Bradshaw.

Pulse Scope IO is licensed under the **GNU General Public License, version 3 only (GPL-3.0-only)**. See [LICENSE](LICENSE) for the full terms. If you distribute a modified version or binaries, follow the GPL requirements, including making the corresponding source available. Third-party dependencies retain their own licences.

[L33T Group UK](https://www.L33T.uk)
