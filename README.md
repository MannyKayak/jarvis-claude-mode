# Jarvis mode

Reads [Claude Code](https://claude.com/claude-code)'s replies aloud at the end of each turn. The built-in `/voice` is input only: Jarvis mode covers the output.

Claude ends every reply with a one- or two-sentence summary written to be listened to, and only that part is spoken: no code, paths or tables recited out loud. As soon as you send a new message, the voice stops.

## Requirements

- Claude Code
- Python 3.8 or later (the installer offers to install it if missing)
- A speech engine: built into Windows and macOS; on Linux you need `espeak-ng`

By default there are no dependencies and no external services: speech synthesis is the operating system's own. More natural online voices are optional, see [Speech engines](#speech-engines).

## Installation

One line, no git needed.

Windows (PowerShell):

```
irm https://raw.githubusercontent.com/MannyKayak/jarvis-claude-mode/main/install.ps1 | iex
```

macOS and Linux:

```
curl -fsSL https://raw.githubusercontent.com/MannyKayak/jarvis-claude-mode/main/install.sh | sh
```

Prefer to read the code first? Clone the repository and run the launcher:

```
git clone https://github.com/MannyKayak/jarvis-claude-mode.git
cd jarvis-claude-mode
.\jarvis-mode install        # Windows
./jarvis-mode install        # macOS and Linux
```

Either way the installation is guided and changes nothing without asking. In order, it:

1. looks for Python and, if it is missing, offers to install it (`winget` on Windows, Homebrew on macOS, your package manager on Linux, where it also offers `espeak-ng`);
2. checks Claude Code, the speech engine and the installed voices;
3. shows what will change and waits for confirmation;
4. lets you pick the speech engine, the language, the voice (with an audio preview) and the assistant's name;
5. asks whether to turn Jarvis mode on right away.

Then open a new Claude Code session.

| Command | Effect |
| --- | --- |
| `jarvis-mode install` | guided installation; run it again to update |
| `jarvis-mode install --dry-run` | shows what would change, without writing anything |
| `jarvis-mode install --yes` | no questions, suggested values |
| `jarvis-mode uninstall` | removes everything |

No clone at hand? Uninstall with the copy kept next to the skill:

```
python ~/.claude/skills/jarvis-mode/install.py uninstall
```

### What gets touched

Everything lives in `~/.claude`, so it works in every repo:

- `skills/jarvis-mode/`: the script, the `/jarvis-mode` command and a copy of the installer;
- `settings.json`: the `Stop` and `UserPromptSubmit` hooks are added. The file is merged, never overwritten, and a copy is saved first as `settings.json.bak-<date>`;
- `jarvis.json`, `jarvis.on`, `jarvis.pid`, `jarvis.ack`: settings and state;
- with the online voices only: `jarvis-venv/` (the edge-tts package) and `jarvis-cache/` (the stock phrases, pre-generated).

## Usage

```
/jarvis-mode              turns it on or off
/jarvis-mode on|off|status
/jarvis-mode setting      changes language, voice and name
```

### No silent waits

The moment you send a message, Jarvis says a short phrase such as "Sure, let me take a look", while Claude is already working on the reply. If the reply still has not come three seconds after that, it adds one "Still thinking". Both stop as soon as the reply arrives. These are stock phrases spoken by the script itself: they cost no tokens and do not slow Claude down.

Available in English and Italian; with other languages Jarvis simply waits in silence. Turn it off with `/jarvis-mode setting`.

### Voice commands

Address the assistant by name, typed or dictated with `/voice`, and it switches itself:

| Say | Effect |
| --- | --- |
| `Jarvis, sleep` · `Jarvis, go to sleep` · `Jarvis, dormi` | turns Jarvis mode off |
| `Jarvis, wake up` · `Jarvis, svegliati` | turns it back on |

Use the name you gave the assistant; `Hey` in front is fine. The command must be the whole message: "Jarvis, sleep and then summarize this" goes to Claude as usual. The message is handled by Jarvis itself and never reaches Claude.

### Settings

`/jarvis-mode setting` lists the available voices, grouped by language, and lets you pick:

- **engine**: the system voices or Microsoft's online ones, see [Speech engines](#speech-engines);
- **language**: only those with at least one voice; it is also the language of the spoken summary;
- **voice**: one of those available for the chosen language;
- **name**: what the voice assistant is called (default `Jarvis`).

To get more voices:

- **Windows**: Settings → Time & language → Speech → Add voices;
- **macOS**: System Settings → Accessibility → Spoken Content → System voice → Manage Voices.

## How it works

- **`UserPromptSubmit` hook**: stops any playback in progress and, when Jarvis is on, asks Claude to end its reply with `<spoken>…</spoken>`.
- **`Stop` hook**: speaks only the content of `<spoken>`. If the tag is missing, it strips code, markdown, URLs and paths and speaks roughly the first 300 characters. Subagent turns are ignored.
- The voice runs detached from the session, which never waits for it. Only the process started by Jarvis is terminated, identified by its PID and start time.
- Any script error is silent: a broken hook never blocks Claude Code.

## Speech engines

You pick the engine during installation or with `/jarvis-mode setting`.

**`system`** (default): the operating system's voices. Offline, nothing to install, but they sound synthetic.

| System | Engine |
| --- | --- |
| Windows | PowerShell: system voices (OneCore), then SAPI "Desktop" voices |
| macOS | `say` |
| Linux | `espeak-ng` |

**`edge`**: Microsoft's online neural voices, through the [edge-tts](https://github.com/rany2/edge-tts) package. Far more natural, hundreds of voices in over a hundred languages, free and without an account. Before choosing it, know that:

- it needs an internet connection, and **every spoken summary is sent to Microsoft**;
- it relies on an unofficial service that may change or stop working. When it is unreachable, Jarvis falls back to the system voice;
- choosing it downloads `edge-tts` into a private virtual environment, `~/.claude/jarvis-venv`, removed on uninstall;
- on Linux it needs an audio player (`ffplay`, `mpg123` or `mpv`) and, on Debian or Ubuntu, the `python3-venv` package. The installer checks for both and offers to install what is missing.

To use yet another engine (piper, ElevenLabs, OpenAI…), set `JARVIS_TTS_CMD` to a shell command that receives the text as UTF-8 on stdin. It can go in the `env` key of `~/.claude/settings.json`:

```json
{ "env": { "JARVIS_TTS_CMD": "espeak-ng -v en -s 150 --stdin" } }
```

## Troubleshooting

Try the speech engine outside Claude Code:

```
python ~/.claude/skills/jarvis-mode/jarvis.py say "Reading test."
python ~/.claude/skills/jarvis-mode/jarvis.py voices
```

- **It doesn't speak**: check with `/hooks` that both hooks are registered and with `/jarvis-mode status` that it is on.
- **`/jarvis-mode` doesn't exist**: open a new session after installing.
- **You moved or upgraded Python**: run `jarvis-mode install` again; the hooks point to the interpreter used to install.

Tested on Windows 11. macOS and Linux support is implemented but not yet verified on those platforms.

## License

[MIT](LICENSE)
