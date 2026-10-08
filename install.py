#!/usr/bin/env python3
"""Installs Jarvis mode at user level (~/.claude), so it works in every repo.

  python install.py init         guided installation (default)
  python install.py uninstall    removes the skill, the hooks and the state files

Options:
  --yes, -y    no questions: accept the suggested values
  --dry-run    show what would change, without writing anything

settings.json is always merged, never overwritten: a copy is saved next to the
original before every change.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jarvis  # noqa: E402

CLAUDE_DIR = jarvis.CLAUDE_DIR
SETTINGS = CLAUDE_DIR / "settings.json"
SKILL_DIR = CLAUDE_DIR / "skills" / "jarvis-mode"
SCRIPT = SKILL_DIR / "jarvis.py"
SOURCE = Path(__file__).resolve().parent / "jarvis.py"
EVENTS = (("Stop", "stop"), ("UserPromptSubmit", "prompt"))
STATE_FILES = ("jarvis.on", "jarvis.pid", "jarvis.json")

SKILL_MD = """---
name: jarvis-mode
description: Turns Jarvis mode on or off, or configures it (language, voice, name). Jarvis mode reads replies aloud at the end of each turn.
argument-hint: [on|off|status|setting]
disable-model-invocation: true
allowed-tools: Bash({cmd} *)
---

!`{cmd} toggle $ARGUMENTS`
"""

YES = False


class Abort(Exception):
    pass


# --- questions -------------------------------------------------------------

def ask(prompt, default=""):
    if YES:
        return default
    try:
        answer = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise Abort("Interrupted. Run the command again to resume: finished steps are not repeated.")
    return answer or default


def confirm(prompt, default=True):
    if YES:
        return True
    answer = ask(f"{prompt} {'[Y/n]' if default else '[y/N]'}").lower()
    return default if not answer else answer[0] == "y"


def choose(prompt, options, default=0):
    """Pick from a numbered list: accepts the number or the exact text."""
    if YES:
        return default
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    while True:
        answer = ask(prompt, str(default + 1))
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        matches = [i for i, o in enumerate(options) if o.lower() == answer.lower()]
        if matches:
            return matches[0]
        print(f"  Invalid choice: type a number from 1 to {len(options)}.")


# --- settings.json ---------------------------------------------------------

def is_ours(entry):
    return "jarvis.py" in json.dumps(entry)


def load_settings():
    if not SETTINGS.exists():
        return {}
    settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    if not isinstance(settings, dict):
        raise ValueError("settings.json does not contain a JSON object")
    return settings


def merge_hooks(settings, install):
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" in settings.json is not an object')
    for event, sub in EVENTS:
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValueError(f'"hooks.{event}" in settings.json is not a list')
        entries = [e for e in entries if not is_ours(e)]
        if install:
            # Exec form (command + args): no shell in between, no quoting problems.
            entries.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": sys.executable,
                            "args": [str(SCRIPT), sub],
                            "timeout": 10,
                        }
                    ]
                }
            )
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        del settings["hooks"]
    return settings


def write_settings(settings, before):
    after = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if after == before:
        return None
    backup = None
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
    tmp = SETTINGS.with_name("settings.json.jarvis-tmp")
    tmp.write_text(after, encoding="utf-8")
    os.replace(tmp, SETTINGS)
    return backup


def skill_md():
    def quote(path):
        path = Path(path).as_posix()
        return f'"{path}"' if " " in path else path

    return SKILL_MD.format(cmd=f"{quote(sys.executable)} {quote(SCRIPT)}")


# --- guided installation steps ---------------------------------------------

def check_environment():
    """Preliminary checks. Returns the installed voices."""
    print("\n1. Checking the environment")
    if sys.version_info < (3, 8):
        raise Abort(f"Python 3.8 or later is required: this is {sys.version.split()[0]}.")
    print(f"   Python {sys.version.split()[0]}: {sys.executable}")

    if CLAUDE_DIR.exists() or shutil.which("claude"):
        print(f"   Claude Code: {CLAUDE_DIR}")
    else:
        print(f"   Claude Code not found: {CLAUDE_DIR} does not exist and 'claude' is not on PATH.")
        if not confirm("   Install anyway?", default=False):
            raise Abort("Install Claude Code first, then run this command again.")

    custom = os.environ.get("JARVIS_TTS_CMD", "").strip()
    if custom:
        print("   Speech engine: custom command (JARVIS_TTS_CMD)")
    engine = {"win32": "powershell.exe", "darwin": "say"}.get(sys.platform, "espeak-ng")
    voices = []
    if shutil.which(engine):
        try:
            voices = jarvis.list_voices()
        except Exception:
            pass
        langs = sorted({v["lang"] for v in voices})
        print(f"   Speech engine: {engine}, {len(voices)} voices in {len(langs)} languages")
    elif not custom:
        print(f"   Speech engine '{engine}' not found: without it, Jarvis stays silent.")
        if engine == "espeak-ng":
            print("   To install it: sudo apt install espeak-ng (or your distro's package manager).")
        print("   Alternatively, set JARVIS_TTS_CMD to a command of your own.")
        if not confirm("   Install anyway?", default=False):
            raise Abort("Install the speech engine, then run this command again.")
    return voices


def show_plan(settings_before):
    print("\n2. What will change")
    print(f"   {'Update' if SCRIPT.exists() else 'Create'} {SKILL_DIR} (jarvis.py, install.py, SKILL.md)")
    hooks = settings_before.get("hooks", {}) if isinstance(settings_before.get("hooks"), dict) else {}
    already = all(any(is_ours(e) for e in hooks.get(event, [])) for event, _ in EVENTS)
    if already:
        print(f"   Stop and UserPromptSubmit hooks already in {SETTINGS}: they will be refreshed.")
    else:
        print(f"   Add the Stop and UserPromptSubmit hooks to {SETTINGS}")
    others = sum(len([e for e in hooks.get(event, []) if not is_ours(e)]) for event, _ in EVENTS)
    if others:
        print(f"   The {others} hook(s) already on those events stay where they are.")
    if SETTINGS.exists():
        print("   Every other setting stays untouched; a copy of settings.json is saved first.")


def install_files(settings, before):
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    # install.py goes along too, so uninstalling works without the original download.
    for source in (SOURCE, Path(__file__).resolve()):
        target = SKILL_DIR / source.name
        if source != target.resolve():
            shutil.copy2(source, target)
    (SKILL_DIR / "SKILL.md").write_text(skill_md(), encoding="utf-8")
    backup = write_settings(settings, before)
    print(f"   Installed in {SKILL_DIR}")
    if backup:
        print(f"   Copy of settings.json: {backup}")


def edge_prerequisites():
    """Linux only: what the online voices still need, as (what it is, package name)."""
    missing = []
    if not sys.platform.startswith("linux"):
        return missing
    if subprocess.run([sys.executable, "-c", "import venv, ensurepip"], capture_output=True).returncode:
        missing.append(("Python's venv module", "python3-venv"))
    if not jarvis.audio_player():
        missing.append(("an audio player", "mpg123"))
    return missing


def package_command(packages):
    """Command installing the packages with this system's package manager, or None."""
    is_root = getattr(os, "geteuid", lambda: 1)() == 0
    sudo = ["sudo"] if not is_root and shutil.which("sudo") else []
    managers = (("apt-get", ["install", "-y"]), ("dnf", ["install", "-y"]),
                ("pacman", ["-S", "--noconfirm"]), ("zypper", ["install", "-y"]))
    for manager, args in managers:
        if shutil.which(manager):
            # Only Debian-style systems ship venv as a separate package.
            wanted = [p for p in packages if p != "python3-venv" or manager == "apt-get"]
            return sudo + [manager] + args + wanted if wanted else None
    return None


def prepare_edge():
    """Offers to install what the online voices need. Returns True when everything is there."""
    missing = edge_prerequisites()
    if not missing:
        return True
    print("   The online voices also need: " + " and ".join(what for what, _ in missing) + ".")
    command = package_command([package for _, package in missing])
    if command:
        if confirm(f"   Install now with: {' '.join(command)} ?"):
            subprocess.run(command)
    else:
        packages = [package for _, package in missing]
        print("   Install with your package manager: " + ", ".join(packages)
              + (" (ffplay or mpv work as the player too)." if "mpg123" in packages else "."))
    if edge_prerequisites():
        print("   Still missing: staying with the system voices. Run this installer again "
              "once they are installed.")
        return False
    return True


def configure(voices):
    print("\n3. Engine, language, voice and name")
    cfg = jarvis.load_config()
    if jarvis.CONFIG.exists():
        print(f"   Current settings: name {cfg['name']}, engine {cfg['engine']}, "
              f"language {cfg['lang']}, voice {cfg['voice'] or 'system default'}")
        if YES or not confirm("   Change them?", default=False):
            return cfg

    print("   Speech engines:")
    engine = jarvis.ENGINES[choose("   Engine", [
        "System voices: built in, work offline, sound synthetic",
        "Microsoft online neural voices: far more natural; they need internet, send every "
        "spoken summary to Microsoft, and download the edge-tts package",
    ], jarvis.ENGINES.index(cfg["engine"]))]
    if engine == "edge" and not prepare_edge():
        engine = "system"
    if engine == "edge":
        print("   Setting up the online voices ...")
        online = jarvis.list_voices("edge") if jarvis.install_edge() else []
        if online:
            voices = online
        else:
            print("   Could not set them up (no connection, or Python's venv module is missing): "
                  "staying with the system voices.")
            engine = "system"
    if engine != cfg["engine"]:
        cfg["voice"] = ""
    cfg["engine"] = engine

    if voices:
        langs = sorted({v["lang"] for v in voices}, key=str.lower)
        # Suggest the current/system language, or at least one with the same prefix.
        preferred = [i for i, lang in enumerate(langs) if lang.lower() == cfg["lang"].lower()] or [
            i for i, lang in enumerate(langs) if lang[:2].lower() == cfg["lang"][:2].lower()
        ]
        if len(langs) > 20:
            suggested = langs[preferred[0]] if preferred else "en-US"
            while True:
                answer = ask(f"   Language tag ({len(langs)} available, like en-US or it-IT)", suggested)
                match = [lang for lang in langs if lang.lower() == answer.lower()]
                if match:
                    cfg["lang"] = match[0]
                    break
                close = [lang for lang in langs if lang[:2].lower() == answer[:2].lower()]
                print("   No voices for that tag." + (f" Similar: {', '.join(close)}" if close else ""))
        elif len(langs) > 1:
            print("   Languages with at least one voice:")
            cfg["lang"] = langs[choose("   Language", langs, preferred[0] if preferred else 0)]
        else:
            cfg["lang"] = langs[0]
            print(f"   Only language with voices: {cfg['lang']}")

        in_lang = [v for v in voices if v["lang"] == cfg["lang"]]
        labels = [jarvis.voice_label(v) for v in in_lang]
        if len(in_lang) == 1:
            cfg["voice"] = in_lang[0]["name"]
            print(f"   Only voice for {cfg['lang']}: {labels[0]}")
        else:
            print(f"   Voices for {cfg['lang']}:")
            pick = 0
            while True:
                pick = choose("   Voice", labels, pick)
                cfg["voice"] = in_lang[pick]["name"]
                if YES:
                    break
                try:
                    jarvis.speak(jarvis.sample(cfg), cfg)
                except Exception:
                    pass
                if confirm(f"   Did you hear it? Keep {cfg['voice']}?"):
                    break
    else:
        print("   No voices detected: the system default will be used.")

    while True:
        name = jarvis.clean_name(ask("   Voice assistant name", cfg["name"]))
        if name:
            cfg["name"] = name
            break
        print("   Invalid name: use letters, digits and spaces.")
    jarvis.save_config(cfg)
    return cfg


def cmd_init(dry_run):
    print("Jarvis mode: guided installation")
    print("Reads Claude Code's replies aloud at the end of each turn.")
    voices = check_environment()

    before = SETTINGS.read_text(encoding="utf-8-sig") if SETTINGS.exists() else ""
    try:
        current = load_settings()
        settings = merge_hooks(json.loads(json.dumps(current)), install=True)
    except ValueError as error:
        raise Abort(f"Leaving {SETTINGS} untouched: {error}. Fix the file and run again.")
    show_plan(current)

    if dry_run:
        print("\n[dry-run] SKILL.md:\n" + skill_md())
        print("[dry-run] resulting hooks:")
        print(json.dumps(settings.get("hooks", {}), indent=2, ensure_ascii=False))
        print("[dry-run] Nothing was changed.")
        return
    if not confirm("\n   Proceed?"):
        raise Abort("Nothing was changed.")
    install_files(settings, before)

    cfg = configure(voices)

    print("\n4. Activation")
    if jarvis.FLAG.exists():
        print("   Jarvis mode is already on.")
    elif confirm("   Turn Jarvis mode on now?"):
        jarvis.FLAG.touch()
    if jarvis.FLAG.exists() and not YES:
        try:
            jarvis.speak(jarvis.greeting(cfg), cfg)
        except Exception:
            pass

    print(f"\nDone. {jarvis.status_line(cfg)}")
    print("Open a new Claude Code session. Commands:")
    print("  /jarvis-mode              turns it on or off")
    print("  /jarvis-mode on|off|status")
    print("  /jarvis-mode setting      changes language, voice and name")


def cmd_uninstall(dry_run):
    print("Jarvis mode: uninstall")
    before = SETTINGS.read_text(encoding="utf-8-sig") if SETTINGS.exists() else ""
    try:
        settings = merge_hooks(load_settings(), install=False)
    except ValueError as error:
        raise Abort(f"Leaving {SETTINGS} untouched: {error}. Fix the file and run again.")
    print(f"   Remove {SKILL_DIR}")
    print(f"   Remove Jarvis hooks from {SETTINGS} (the others stay)")
    print(f"   Remove state and settings: {', '.join(STATE_FILES)}")
    if jarvis.VENV.exists():
        print(f"   Remove the online voices environment: {jarvis.VENV}")
    if dry_run:
        print("[dry-run] Nothing was changed.")
        return
    if not confirm("   Proceed?"):
        raise Abort("Nothing was changed.")
    jarvis.stop_speaking()
    shutil.rmtree(SKILL_DIR, ignore_errors=True)
    shutil.rmtree(jarvis.VENV, ignore_errors=True)
    for name in STATE_FILES:
        try:
            (CLAUDE_DIR / name).unlink()
        except OSError:
            pass
    backup = write_settings(settings, before)
    if backup:
        print(f"   Copy of settings.json: {backup}")
    print("Jarvis mode uninstalled.")


def main():
    global YES
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("-")}
    commands = [a for a in args if not a.startswith("-")]
    unknown = flags - {"--yes", "-y", "--dry-run", "--uninstall", "--help", "-h"}
    command = "uninstall" if "--uninstall" in flags else (commands[0] if commands else "init")
    if unknown or flags & {"--help", "-h"} or command not in ("init", "install", "uninstall"):
        print(__doc__.strip())
        return 0 if flags & {"--help", "-h"} else 2
    YES = bool(flags & {"--yes", "-y"})
    try:
        if command == "uninstall":
            cmd_uninstall("--dry-run" in flags)
        else:
            cmd_init("--dry-run" in flags)
    except Abort as stop:
        print(f"\n{stop}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
