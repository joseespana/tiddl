# Tidal Downloader

Download tracks and videos from Tidal with max quality! `tiddl` ships with **two interfaces** — a CLI (original) and a modern desktop GUI built with PySide6. Pick whichever fits your workflow.

> [!WARNING]
> `This app is for personal use only and is not affiliated with Tidal. Users must ensure their use complies with Tidal's terms of service and local copyright laws. Downloaded tracks are for personal use and may not be shared or redistributed. The developer assumes no responsibility for misuse of this app.`

![PyPI - Downloads](https://img.shields.io/pypi/dm/tiddl?style=for-the-badge&color=%2332af64)
![PyPI - Version](https://img.shields.io/pypi/v/tiddl?style=for-the-badge)
[<img src="https://img.shields.io/badge/gitmoji-%20😜%20😍-FFDD67.svg?style=for-the-badge" />](https://gitmoji.dev)

# Installation

`tiddl` is published on [PyPI](https://pypi.org/project/tiddl/). Pick the flow that matches your platform.

> [!IMPORTANT]
> You also need [`ffmpeg`](https://ffmpeg.org/download.html) on your PATH — it converts downloaded tracks to their final format. On macOS: `brew install ffmpeg`. On Windows: download from ffmpeg.org and add the `bin/` folder to PATH, or `winget install ffmpeg`. On Linux: `sudo apt install ffmpeg` / equivalent.

## Using pip (any OS)

**macOS / Linux**

```bash
python3 -m pip install tiddl
```

**Windows (PowerShell)**

```powershell
python -m pip install tiddl
```

> [!NOTE]
> If `python` isn't recognised on Windows, install it from [python.org](https://www.python.org/downloads/) and tick *"Add python.exe to PATH"*, or use the bundled launcher `py -3 -m pip install tiddl`.

## Using uv (optional, faster)

[`uv`](https://docs.astral.sh/uv/) is a fast drop-in replacement for pip. Works on macOS, Linux and Windows:

```bash
uv tool install tiddl
```

## Docker

**coming soon**

# Usage

You have two ways to run tiddl:

| Mode | Command (macOS/Linux) | Command (Windows) | Best for |
| --- | --- | --- | --- |
| **CLI** (console) | `tiddl …` | `tiddl …` | Scripts, servers, power users |
| **GUI** (desktop app) | `python3 -m app.main` | `python -m app.main` | Browsing your library, bulk selecting downloads |

Both share the same auth tokens and config (`~/.tiddl/` on macOS/Linux, `%USERPROFILE%\.tiddl\` on Windows), so you can switch between CLI and GUI freely.

## GUI (desktop app)

The GUI lets you browse your Tidal library (Your Playlists, Liked Playlists, Albums, Artists), see which items are already downloaded, re-download, and watch per-track progress in real time.

### Requirements

- **Python 3.10+**
- **ffmpeg** on your PATH — [download here](https://ffmpeg.org/download.html). The GUI uses the same tiddl engine under the hood, so ffmpeg is still required to convert tracks.
- **PySide6** — listed in `pyproject.toml`, installed automatically in the step below.

> [!NOTE]
> Throughout this section the commands are written as `python3` / `python3 -m pip`. Use whichever alias your system has:
> - **macOS / Linux**: `python3` and `python3 -m pip` (the bare `python` / `pip` are usually not on PATH)
> - **Windows (PowerShell / cmd)**: `python` and `python -m pip`, or the Windows launcher `py -3`

### Install

Clone the repo and install in editable mode:

**macOS / Linux**

```bash
git clone https://github.com/oskvr37/tiddl
cd tiddl
python3 -m pip install -e .
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/oskvr37/tiddl
cd tiddl
python -m pip install -e .
```

If you get `'python' is not recognised` on Windows, install Python from [python.org](https://www.python.org/downloads/) and tick *"Add python.exe to PATH"*, or use `py -3 -m pip install -e .`.

### Run it

From the repo root:

**macOS / Linux**

```bash
python3 -m app.main
```

**Windows**

```powershell
python -m app.main
```

On first launch a "Connect with Tidal" dialog opens: click the button, approve in your browser, and the app remembers your session in `~/.tiddl/auth.json` (or `%USERPROFILE%\.tiddl\auth.json` on Windows).

### Features

- Sidebar tabs: **Playlists** · **Albums** · **Artists** · **Downloaded** · **Search Tidal**
- Playlists sub-tabs: **All** · **Your Playlists** (created by you, paginated) · **Liked** (favorited)
- Type pills on every row (PLAYLIST / ALBUM / ARTIST) and a ✓ Downloaded badge for items already on disk
- Real-time download status card: current track, quality, N/M items, track counter
- Concurrent downloads (3 in parallel by default) via `QThreadPool`
- Auto re-login when your Tidal token expires
- Auto-refresh of the Downloaded tab when a download finishes

### Screenshot

*(GUI screenshot here — run the app to see it in action.)*

## CLI

Run the app with `tiddl`

```bash
$ tiddl
 Usage: tiddl [OPTIONS] COMMAND [ARGS]...

 tiddl - download tidal tracks ♫

╭─ Options ───────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --omit-cache            --no-omit-cache      [default: no-omit-cache]                                       │
│ --debug                 --no-debug           [default: no-debug]                                            │
│ --install-completion                         Install completion for the current shell.                      │
│ --show-completion                            Show completion for the current shell, to copy it or customize │
│                                              the installation.                                              │
│ --help                                       Show this message and exit.                                    │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ──────────────────────────────────────────────────────────────────────────────────────────────────╮
│ auth       Manage Tidal authentication.                                                                     │
│ download   Download Tidal resources.                                                                        │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

## Authentication

Login to app with your Tidal account: run the command below and follow instructions.

```bash
tiddl auth login
```

## Downloading

You can download tracks / videos / albums / artists / playlists / mixes.

```bash
$ tiddl download url <url>
```

> [!TIP]
> You don't have to paste full urls, track/103805726, album/103805723 etc. will also work

Run `tiddl download` to see available download options.

### Error Handling

By default, tiddl stops when encountering unavailable items in collections such as playlists, albums, artists, or mixes (e.g., removed or region-locked tracks).

Use `--skip-errors` to automatically skip these items and continue downloading:

```bash
tiddl download url <url> --skip-errors
```

Skipped items are logged with track/album name and IDs for reference.

### Quality

| Quality | File extension |        Details        |
| :-----: | :------------: | :-------------------: |
|   LOW   |      .m4a      |        96 kbps        |
| NORMAL  |      .m4a      |       320 kbps        |
|  HIGH   |     .flac      |   16-bit, 44.1 kHz    |
|   MAX   |     .flac      | Up to 24-bit, 192 kHz |

### Output

You can format filenames of your downloaded resources and put them in different directories.

For example, setting output flag to `"{album.artist}/{album.title}/{item.number:02d}. {item.title}"`
will download tracks like following:

```
Music
└── Kanye West
    └── Graduation
        ├── 01. Good Morning.flac
        ├── 02. Champion.flac
        ├── 03. Stronger.flac
        ├── 04. I Wonder.flac
        ├── 05. Good Life.flac
        ├── 06. Can't Tell Me Nothing.flac
        ├── 07. Barry Bonds.flac
        ├── 08. Drunk and Hot Girls.flac
        ├── 09. Flashing Lights.flac
        ├── 10. Everything I Am.flac
        ├── 11. The Glory.flac
        ├── 12. Homecoming.flac
        ├── 13. Big Brother.flac
        └── 14. Good Night.flac
```

> [!NOTE]
> Learn more about [file templating](/docs/templating.md)

## Configuration files

Files of the app are created in your home directory. By default, the app is located at `~/.tiddl`.

You can (and should) create the `config.toml` file to configure the app how you want.

You can copy example config from docs [config.example.toml](/docs/config.example.toml)

## Environment variables

### Custom app path

You can set `TIDDL_PATH` environment variable to use custom path for `tiddl` app.

**macOS / Linux** (bash/zsh)

```bash
TIDDL_PATH=~/custom/tiddl tiddl auth login
```

**Windows (PowerShell)**

```powershell
$env:TIDDL_PATH = "$HOME\custom\tiddl"; tiddl auth login
```

**Windows (cmd.exe)**

```cmd
set TIDDL_PATH=%USERPROFILE%\custom\tiddl && tiddl auth login
```

### Auth stopped working?

Set `TIDDL_AUTH` environment variable to use another credentials:

```
TIDDL_AUTH=<CLIENT_ID>;<CLIENT_SECRET>
```

(Export it the same way as `TIDDL_PATH` above for your shell.)

# Development

Clone the repository:

```bash
git clone https://github.com/oskvr37/tiddl
cd tiddl
```

Create and activate a virtual environment:

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows (cmd.exe)**

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

Install the package in editable mode:

```bash
python -m pip install -e .
```

> [!TIP]
> If you prefer a faster install, [`uv`](https://docs.astral.sh/uv/) is a drop-in pip replacement written in Rust. After installing uv you can use `uv venv` + `uv pip install -e .` instead of the commands above.

# Resources

[Tidal API wiki (api endpoints)](https://github.com/Fokka-Engineering/TIDAL)

[Tidal-Media-Downloader (inspiration)](https://github.com/yaronzz/Tidal-Media-Downloader)
