#!/usr/bin/env python3
"""Jarvis mode: reads Claude Code's replies aloud (TTS) at the end of each turn.

Subcommands:
  stop                     Stop hook: speaks <spoken>...</spoken> from the last reply
  prompt                   UserPromptSubmit hook: silences playback and injects the instruction
  toggle [on|off|status|setting]   switch (no argument flips the state)
  config [--engine E] [--lang X] [--voice Y] [--name Z]   saves engine, language, voice, name
  voices [engine] [language]   lists available voices as JSON
  say <text>               tries the TTS engine, ignoring the switch

Environment variables:
  JARVIS_TTS_CMD   shell command that receives the text (UTF-8) on stdin; replaces the default engine
  JARVIS_VOICE     voice for the default engine, used when none was picked with "setting"

Speech engines:
  system   the operating system's voices, offline (default)
  edge     Microsoft's online neural voices through the edge-tts package, installed on demand
           into a private virtual environment; each spoken text is sent to Microsoft

A hook must never break the session: any error -> silent exit 0.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
FLAG = CLAUDE_DIR / "jarvis.on"
PIDFILE = CLAUDE_DIR / "jarvis.pid"
CONFIG = CLAUDE_DIR / "jarvis.json"
CONFIG_KEYS = ("engine", "lang", "voice", "name")
ENGINES = ("system", "edge")
VENV = CLAUDE_DIR / "jarvis-venv"
EDGE_MARKER = VENV / "edge-tts.ok"

LINUX_PLAYERS = (
    ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"),
    ("mpg123", "-q"),
    ("mpv", "--no-video", "--really-quiet"),
)

# Hand-picked online voices, one male and one female, listed first and offered by default.
EDGE_PICKS = {
    "it-IT": ("it-IT-GiuseppeMultilingualNeural", "it-IT-ElsaNeural"),
}

WINDOWS = sys.platform == "win32"
FALLBACK_CHARS = 300
SPOKEN_CHARS = 1200

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

INSTRUCTION = (
    "Jarvis mode is on: your reply will be read aloud. "
    "You are {name}, the user's voice assistant: if asked your name, answer {name}. "
    "ALWAYS end the final reply of the turn with a <spoken>...</spoken> block on its own "
    "line: 1-2 full, conversational sentences in the language {lang}, summarizing what you "
    "did or answered. Inside the tag: no code, paths, URLs, markdown, lists or "
    "unpronounceable acronyms. The tag content is exempt from any compressed or terse "
    "style rule: write it the way you would say it out loud. "
    "Do not mention the tag in the rest of the reply."
)

# Phrases spoken by the tool itself, by language prefix; English otherwise.
GREETINGS = {"it": "{name} è al tuo servizio.", "en": "{name} is ready."}
SAMPLES = {"it": "Questa è la mia voce.", "en": "This is my voice."}

# Voice and language come from JARVIS_VOICE / JARVIS_LANG. The text is read from stdin as raw
# bytes, so the console encoding never touches accented characters. OneCore voices (the ones
# in Windows settings) are tried first, then the SAPI "Desktop" voices.
PS_SPEAK = r"""
$ErrorActionPreference='Stop'
$m=New-Object IO.MemoryStream
[Console]::OpenStandardInput().CopyTo($m)
$t=[Text.Encoding]::UTF8.GetString($m.ToArray())
$v=$env:JARVIS_VOICE
$l=$env:JARVIS_LANG
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
  $all=[Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
  if($v){$pick=$all|Where-Object{$_.DisplayName -eq $v}|Select-Object -First 1}
  else{$pick=$all|Where-Object{$_.Language -eq $l}|Select-Object -First 1}
  if(-not $pick){throw 'no onecore voice'}
  $s=New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
  $s.Voice=$pick
  $op=$s.SynthesizeTextToStreamAsync($t)
  $as=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object{$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'}|Select-Object -First 1
  $task=$as.MakeGenericMethod([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]).Invoke($null,@($op))
  $null=$task.Wait(20000)
  $w=New-Object IO.MemoryStream
  [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($task.Result).CopyTo($w)
  $w.Position=0
  (New-Object System.Media.SoundPlayer($w)).PlaySync()
} catch {
  Add-Type -AssemblyName System.Speech
  $s=New-Object System.Speech.Synthesis.SpeechSynthesizer
  try{if($v){$s.SelectVoice($v)}else{$s.SelectVoiceByHints('NotSet','NotSet',0,[Globalization.CultureInfo]$l)}}catch{}
  $s.Speak($t)
}
"""

# Plays the audio file named by JARVIS_FILE and waits for it to end.
PS_PLAY = r"""
Add-Type -AssemblyName PresentationCore
$p=New-Object System.Windows.Media.MediaPlayer
$p.Open([Uri]$env:JARVIS_FILE)
$n=0
while(-not $p.NaturalDuration.HasTimeSpan -and $n -lt 50){Start-Sleep -Milliseconds 100;$n++}
if(-not $p.NaturalDuration.HasTimeSpan){exit 1}
$p.Play()
Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds+300)
$p.Close()
"""

PS_VOICES = r"""
try {
  $null=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
  foreach($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices){'onecore|'+$v.DisplayName+'|'+$v.Language+'|'+$v.Gender}
} catch {}
try {
  Add-Type -AssemblyName System.Speech
  foreach($v in (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()){if($v.Enabled){'sapi|'+$v.VoiceInfo.Name+'|'+$v.VoiceInfo.Culture.Name+'|'+$v.VoiceInfo.Gender}}
} catch {}
"""


def system_lang():
    """The user's OS language as a tag like en-US; en-US when it can't be determined."""
    try:
        if WINDOWS:
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value
        else:
            code = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").split(".")[0]
            if "_" in code:
                return code.replace("_", "-")
    except Exception:
        pass
    return "en-US"


def load_config():
    cfg = {"engine": "system", "lang": system_lang(), "voice": "", "name": "Jarvis"}
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in data.items() if k in CONFIG_KEYS and isinstance(v, str) and v})
    except Exception:
        pass
    return cfg


def clean_name(name):
    # The name ends up in the model's context: letters, digits, spaces and a few marks only.
    return re.sub(r"[^\w .'-]", "", name).strip()[:40]


def save_config(cfg):
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def phrase(table, cfg):
    return table.get(cfg["lang"][:2].lower(), table["en"]).format(name=cfg["name"])


def greeting(cfg):
    return phrase(GREETINGS, cfg)


def sample(cfg):
    return phrase(SAMPLES, cfg)


def venv_python():
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def edge_ready():
    return EDGE_MARKER.exists() and venv_python().exists()


def install_edge():
    """Creates the private environment holding edge-tts. Returns True when it is usable."""
    if edge_ready():
        return True
    quiet = dict(capture_output=True, timeout=600)
    if WINDOWS:
        quiet["creationflags"] = CREATE_NO_WINDOW
    try:
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True, **quiet)
        subprocess.run(
            [str(venv_python()), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "edge-tts"],
            check=True,
            **quiet,
        )
        EDGE_MARKER.write_text("ok", encoding="utf-8")
    except Exception:
        return False
    return True


def list_voices(engine="system"):
    """Available voices: [{"name", "lang", "gender"}], sorted by language and name."""
    voices = []
    if engine == "edge":
        if edge_ready():
            flags = {"creationflags": CREATE_NO_WINDOW} if WINDOWS else {}
            out = subprocess.run(
                [str(venv_python()), os.path.abspath(__file__), "edge-voices"],
                capture_output=True,
                timeout=30,
                **flags,
            ).stdout.decode("utf-8", "replace")
            try:
                voices = json.loads(out)
            except ValueError:
                voices = []
    elif WINDOWS:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_VOICES],
            capture_output=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        ).stdout.decode("utf-8", "replace")
        rows = [line.strip().split("|") for line in out.splitlines() if line.count("|") == 3]
        onecore = {r[1] for r in rows if r[0] == "onecore"}
        for engine, name, lang, gender in rows:
            # "Microsoft Elsa Desktop" is the SAPI build of the same voice as "Microsoft Elsa".
            if engine == "sapi" and name.replace(" Desktop", "") in onecore:
                continue
            voices.append({"name": name, "lang": lang, "gender": gender.lower()})
    elif sys.platform == "darwin":
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            m = re.match(r"(.+?)\s{2,}([a-z]{2,3})[_-]([A-Za-z0-9]{2,})\s+#", line)
            if m:
                voices.append({"name": m[1].strip(), "lang": f"{m[2]}-{m[3]}", "gender": ""})
    else:
        out = subprocess.run(
            ["espeak-ng", "--voices"], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4:
                voices.append({"name": cols[1], "lang": cols[1], "gender": ""})
    def rank(v):
        picks = EDGE_PICKS.get(v["lang"], ()) if engine == "edge" else ()
        v["recommended"] = v["name"] in picks
        return picks.index(v["name"]) if v["recommended"] else len(picks)

    return sorted(voices, key=lambda v: (v["lang"].lower(), rank(v), v["name"].lower()))


def voice_label(voice):
    notes = [n for n in (voice["gender"], "recommended" if voice.get("recommended") else "") if n]
    return voice["name"] + (f" ({', '.join(notes)})" if notes else "")


def tts_command(lang, voice):
    """System engine command and the environment to launch it with."""
    env = dict(os.environ, JARVIS_VOICE=voice, JARVIS_LANG=lang)
    if WINDOWS:
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_SPEAK], env
    if sys.platform == "darwin":
        return ["say"] + (["-v", voice] if voice else []), env
    return ["espeak-ng", "-v", voice or lang.split("-")[0], "--stdin"], env


def audio_player():
    """Command that plays an audio file on macOS and Linux; None when there is none."""
    if sys.platform == "darwin":
        return ["afplay"]
    for player in LINUX_PLAYERS:
        if shutil.which(player[0]):
            return list(player)
    return None


def play_file(path):
    """Plays an audio file and waits for it to end. Raises when no player is available."""
    if WINDOWS:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_PLAY]
        subprocess.run(cmd, env=dict(os.environ, JARVIS_FILE=path), check=True,
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        return
    player = audio_player()
    if not player:
        raise RuntimeError("no audio player")
    subprocess.run(player + [path], check=True, capture_output=True)


def cmd_edge_play():
    """Runs inside the private environment: synthesizes stdin with edge-tts and plays it."""
    text = sys.stdin.buffer.read().decode("utf-8", "replace")
    lang = os.environ.get("JARVIS_LANG", "") or system_lang()
    # A playback killed midway leaves its file behind: sweep those up.
    for stale in Path(tempfile.gettempdir()).glob("jarvis-*.mp3"):
        try:
            stale.unlink()
        except OSError:
            pass
    fd, path = tempfile.mkstemp(prefix="jarvis-", suffix=".mp3")
    os.close(fd)
    try:
        try:
            import asyncio

            import edge_tts

            talk = edge_tts.Communicate(text, os.environ.get("JARVIS_VOICE", ""))
            asyncio.run(asyncio.wait_for(talk.save(path), 20))
            play_file(path)
        except Exception:
            # Offline, service down or no player: say it with the system voice instead.
            cmd, env = tts_command(lang, "")
            flags = {"creationflags": CREATE_NO_WINDOW} if WINDOWS else {}
            subprocess.run(cmd, input=text.encode("utf-8"), env=env, capture_output=True, **flags)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def cmd_edge_voices():
    import asyncio

    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    print(json.dumps(
        [{"name": v["ShortName"], "lang": v["Locale"], "gender": v["Gender"].lower()} for v in voices]
    ))


def proc_token(pid):
    """Process identity (its start time): guards against killing a recycled PID."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            k.CloseHandle(handle)
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=2
    ).stdout.strip()
    return out or None


def stop_speaking():
    try:
        data = json.loads(PIDFILE.read_text(encoding="utf-8"))
        pid, token = int(data["pid"]), data["token"]
    except Exception:
        pid = token = None
    try:
        PIDFILE.unlink()
    except OSError:
        pass
    if not pid or not token:
        return
    try:
        if proc_token(pid) != token:
            return
        if WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            os.killpg(pid, signal.SIGTERM)
    except Exception:
        pass


def speak(text, cfg=None):
    stop_speaking()
    custom = os.environ.get("JARVIS_TTS_CMD", "").strip()
    cfg = cfg or load_config()
    if cfg["engine"] == "edge" and cfg["voice"] and edge_ready():
        default = [str(venv_python()), os.path.abspath(__file__), "edge-play"]
        env = dict(os.environ, JARVIS_VOICE=cfg["voice"], JARVIS_LANG=cfg["lang"])
    else:
        voice = cfg["voice"] if cfg["engine"] == "system" else ""
        default, env = tts_command(
            cfg["lang"], voice or os.environ.get("JARVIS_VOICE", "").strip()
        )
    kwargs = dict(
        env=env,
        shell=bool(custom),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    cmd = custom or default
    if WINDOWS:
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            # The parent's job object does not allow breakaway.
            proc = subprocess.Popen(cmd, creationflags=flags, **kwargs)
    else:
        proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    PIDFILE.write_text(
        json.dumps({"pid": proc.pid, "token": proc_token(proc.pid)}), encoding="utf-8"
    )
    proc.stdin.write(text.encode("utf-8"))
    proc.stdin.close()


def strip_markdown(text):
    t = re.sub(r"```.*?(?:```|\Z)", " ", text, flags=re.S)
    t = re.sub(r"`[^`\n]*`", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>\n]+>", " ", t)
    t = re.sub(r"\b(?:https?|ftp|file)://\S+|\bwww\.\S+", " ", t)
    # Paths: C:\x, ~/x, ./x, ../x, /a/b, a/b/c, a/b.ext
    t = re.sub(r"(?<![\w/\\])(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/])[^\s,;)\]]*", " ", t)
    t = re.sub(r"(?<![\w/\\])/[\w.-]+(?:/[\w.-]+)+/?", " ", t)
    t = re.sub(r"\b[\w.-]+(?:[\\/][\w.-]+){2,}", " ", t)
    t = re.sub(r"\b[\w.-]+(?:[\\/][\w.-]+)+\.\w{1,6}\b", " ", t)
    t = re.sub(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", " ", t, flags=re.M)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"\*+|~~|\|", " ", t)
    t = re.sub(r"(?<!\w)_+|_+(?!\w)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def truncate(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    if ends and ends[-1] >= limit // 2:
        return cut[: ends[-1]]
    return cut.rsplit(" ", 1)[0]


def spoken_text(message):
    blocks = re.findall(r"<spoken>(.*?)(?:</spoken>|\Z)", message, flags=re.S | re.I)
    for block in reversed(blocks):
        text = strip_markdown(block)
        if text:
            return truncate(text, SPOKEN_CHARS)
    return truncate(strip_markdown(message), FALLBACK_CHARS)


def read_hook_input():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def cmd_stop():
    if not FLAG.exists():
        return
    data = read_hook_input()
    if data.get("agent_id"):  # a subagent's turn
        return
    message = data.get("last_assistant_message")
    if not isinstance(message, str):
        return
    text = spoken_text(message)
    if text:
        speak(text)


def cmd_prompt():
    stop_speaking()
    if FLAG.exists():
        out = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": INSTRUCTION.format(**load_config()),
            }
        }
        print(json.dumps(out))


def self_command():
    def quote(path):
        path = Path(path).as_posix()
        return f'"{path}"' if " " in path else path

    return f"{quote(sys.executable)} {quote(os.path.abspath(__file__))}"


def status_line(cfg):
    state = "ON" if FLAG.exists() else "OFF"
    voice = cfg["voice"] or "system default"
    return (f"Jarvis mode: {state} (name: {cfg['name']}, engine: {cfg['engine']}, "
            f"language: {cfg['lang']}, voice: {voice})")


def voices_by_lang(voices):
    langs = {}
    for v in voices:
        langs.setdefault(v["lang"], []).append(voice_label(v))
    return langs


def print_setup(cfg):
    """Instructions for Claude: the user makes the choices, through AskUserQuestion."""
    me = self_command()
    print(status_line(cfg))
    print("\nSpeech engines:")
    print("- system: the operating system's voices. Works offline. Sounds synthetic.")
    print("- edge: Microsoft's online neural voices, far more natural. Needs internet; every "
          "spoken summary is sent to Microsoft; it is an unofficial free service that may stop "
          "working (Jarvis then falls back to the system voice). "
          + ("Installed." if edge_ready() else
             "Not installed yet: choosing it downloads the edge-tts package into a private "
             "environment."))
    print("\nSystem voices, by language:")
    for lang, names in voices_by_lang(list_voices()).items():
        print(f"- {lang}: {', '.join(names)}")
    if edge_ready():
        edge = voices_by_lang(list_voices("edge"))
        same = [lang for lang in edge if lang[:2].lower() == cfg["lang"][:2].lower()]
        print(f"\nEdge voices ({len(edge)} languages; shown: those matching {cfg['lang']}):")
        for lang in same:
            print(f"- {lang}: {', '.join(edge[lang])}")
    print(
        "\nInstructions for you: configure Jarvis mode by asking the user with the "
        "AskUserQuestion tool, in the language they write in. Do not choose for them.\n"
        "1. Engine: system or edge. Tell them plainly what edge implies (internet, text sent "
        "to Microsoft, package download if not installed).\n"
        f"   If they pick edge and it is not installed, run: {me} config --engine edge\n"
        f"   To list edge voices for a language, run: {me} voices edge <language>\n"
        "2. Language: ask only if the chosen engine has more than one worth offering.\n"
        "3. Voice: offer the chosen engine's voices for that language, in the order listed "
        "and saying which are recommended (4 options at most; "
        "if there are more, offer the first ones and remind them they can type another). "
        "If there is a single voice, use it and say so.\n"
        "4. Voice assistant name: offer the current name and a couple of alternatives; "
        "the user can type their own.\n"
        "Then run this command, with the voice name without the part in parentheses:\n"
        f'{me} config --engine <engine> --lang <language> --voice "<voice>" --name "<name>"\n'
        "The command saves the settings and plays the new voice. Report the outcome in one line."
    )


def cmd_toggle(args):
    arg = args[0].lower() if args else "toggle"
    if arg in ("setting", "settings", "setup", "config"):
        print_setup(load_config())
        return
    if arg not in ("on", "off", "status", "toggle"):
        print("Usage: /jarvis-mode [on|off|status|setting]")
        arg = "status"
    if arg == "toggle":
        arg = "off" if FLAG.exists() else "on"
    if arg == "on":
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        FLAG.touch()
        if not CONFIG.exists():  # first activation
            print_setup(load_config())
            return
    elif arg == "off":
        try:
            FLAG.unlink()
        except OSError:
            pass
        stop_speaking()
    print(status_line(load_config()))
    print("\nReport the status above to the user in a single line. Do nothing else.")


def cmd_config(args):
    cfg = load_config()
    new = dict(zip([a.lstrip("-") for a in args[0::2]], args[1::2]))
    if len(args) % 2 or set(new) - set(CONFIG_KEYS):
        print('Usage: config [--engine system|edge] [--lang <language>] [--voice "<voice>"] '
              '[--name "<name>"]')
        return
    if "engine" in new:
        engine = new["engine"].strip().lower()
        if engine not in ENGINES:
            print(f"Unknown engine: {new['engine']}. Available: {', '.join(ENGINES)}")
            return
        if engine == "edge" and not WINDOWS and not audio_player():
            print("No audio player found: the online voices need ffplay, mpg123 or mpv. "
                  "Install one and try again. Nothing was changed.")
            return
        if engine == "edge" and not install_edge():
            print("Could not install the edge-tts package. Check the internet connection and "
                  "that Python's venv module is available (on Debian or Ubuntu: install the "
                  "python3-venv package). Nothing was changed.")
            return
        if engine != cfg["engine"] and "voice" not in new:
            cfg["voice"] = ""  # the old voice belongs to the other engine
        cfg["engine"] = engine
    voices = list_voices(cfg["engine"])
    if cfg["engine"] == "edge" and not voices:
        print("Could not reach the online voice list. Check the connection and try again.")
        return
    if "lang" in new:
        match = [v["lang"] for v in voices if v["lang"].lower() == new["lang"].strip().lower()]
        if not match:
            available = ", ".join(voices_by_lang(voices))
            print(f"No voices installed for language: {new['lang']}. Available: {available}")
            return
        if match[0] != cfg["lang"] and "voice" not in new:
            cfg["voice"] = ""  # the old voice speaks another language
        cfg["lang"] = match[0]
    if "voice" in new:
        wanted = re.sub(r"\s*\([^)]*\)\s*$", "", new["voice"]).strip().lower()
        match = [v for v in voices if v["name"].lower() == wanted and v["lang"] == cfg["lang"]]
        if not match:
            available = ", ".join(voices_by_lang(voices).get(cfg["lang"], []))
            print(f"No such {cfg['engine']} voice for {cfg['lang']}: {new['voice']}. Available: {available}")
            return
        cfg["voice"] = match[0]["name"]
    if cfg["engine"] == "edge" and not cfg["voice"]:
        # Online voices have no "system default": take the first one for the language.
        match = [v for v in voices if v["lang"] == cfg["lang"]]
        if not match:
            print(f"No edge voices for {cfg['lang']}. Pass --lang with one of: "
                  + ", ".join(voices_by_lang(voices)))
            return
        cfg["voice"] = match[0]["name"]
    if "name" in new:
        name = clean_name(new["name"])
        if not name:
            print("Invalid name: use letters, digits and spaces.")
            return
        cfg["name"] = name
    save_config(cfg)
    print("Saved. " + status_line(cfg))
    speak(greeting(cfg), cfg)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if cmd == "stop":
        cmd_stop()
    elif cmd == "prompt":
        cmd_prompt()
    elif cmd == "toggle":
        cmd_toggle(args[1:])
    elif cmd == "config":
        cmd_config(args[1:])
    elif cmd == "voices":
        rest = args[1:]
        engine = rest.pop(0).lower() if rest and rest[0].lower() in ENGINES else "system"
        lang = rest[0].lower() if rest else ""
        voices = [v for v in list_voices(engine) if not lang or v["lang"].lower() == lang]
        print(json.dumps(voices, ensure_ascii=False))
    elif cmd == "edge-play":
        cmd_edge_play()
    elif cmd == "edge-voices":
        cmd_edge_voices()
    elif cmd == "say":
        speak(" ".join(args[1:]))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
