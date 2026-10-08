# Jarvis mode

Reads [Claude Code](https://claude.com/claude-code)'s replies aloud at the end of each turn. The built-in `/voice` is input only: Jarvis mode covers the output.

Claude ends every reply with a one- or two-sentence summary written to be listened to, and only that part is spoken: no code, paths or tables recited out loud. As soon as you send a new message, the voice stops.

## Requirements

- Claude Code
- Python 3.8 or later
- A speech engine: built into Windows and macOS; on Linux you need `espeak-ng`

No dependencies to install and no external services: speech synthesis is the operating system's own.

## Installation

```
git clone https://github.com/MannyKayak/jarvis-mode.git
cd jarvis-mode
python install.py
```

On macOS and Linux the command is `python3 install.py`.

The installation is guided and changes nothing without asking. In order, it:

1. checks Python, Claude Code, the speech engine and the installed voices;
2. shows what will change and waits for confirmation;
3. lets you pick the language, the voice (with an audio preview) and the assistant's name;
4. asks whether to turn Jarvis mode on right away.

Then open a new Claude Code session.

| Command | Effect |
| --- | --- |
| `python install.py` | guided installation; run it again to update |
| `python install.py --dry-run` | shows what would change, without writing anything |
| `python install.py --yes` | no questions, suggested values |
| `python install.py uninstall` | removes everything |

### What gets touched

Everything lives in `~/.claude`, so it works in every repo:

- `skills/jarvis-mode/`: the script and the `/jarvis-mode` command;
- `settings.json`: the `Stop` and `UserPromptSubmit` hooks are added. The file is merged, never overwritten, and a copy is saved first as `settings.json.bak-<date>`;
- `jarvis.json`, `jarvis.on`, `jarvis.pid`: settings and state.

## Usage

```
/jarvis-mode              turns it on or off
/jarvis-mode on|off|status
/jarvis-mode setting      changes language, voice and name
```

`/jarvis-mode setting` lists the installed voices, grouped by language, and lets you pick:

- **language**: only those with at least one installed voice; it is also the language of the spoken summary;
- **voice**: one of those installed for the chosen language;
- **name**: what the voice assistant is called (default `Jarvis`).

To get more voices:

- **Windows**: Settings → Time & language → Speech → Add voices;
- **macOS**: System Settings → Accessibility → Spoken Content → System voice → Manage Voices.

## How it works

- **`UserPromptSubmit` hook**: stops any playback in progress and, when Jarvis is on, asks Claude to end its reply with `<spoken>…</spoken>`.
- **`Stop` hook**: speaks only the content of `<spoken>`. If the tag is missing, it strips code, markdown, URLs and paths and speaks roughly the first 300 characters. Subagent turns are ignored.
- The voice runs detached from the session, which never waits for it. Only the process started by Jarvis is terminated, identified by its PID and start time.
- Any script error is silent: a broken hook never blocks Claude Code.

## Speech engine

| System | Engine |
| --- | --- |
| Windows | PowerShell: system voices (OneCore), then SAPI "Desktop" voices |
| macOS | `say` |
| Linux | `espeak-ng` |

To use another engine (piper, ElevenLabs, OpenAI…), set `JARVIS_TTS_CMD` to a shell command that receives the text as UTF-8 on stdin. It can go in the `env` key of `~/.claude/settings.json`:

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
- **You moved or upgraded Python**: run `python install.py` again; the hooks point to the interpreter used to install.

Tested on Windows 11. macOS and Linux support is implemented but not yet verified on those platforms.

## License

[MIT](LICENSE)
