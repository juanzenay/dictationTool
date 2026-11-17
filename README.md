# Dictation Tool

Dictation for Windows. Hold the shortcut, speak, release: the text is inserted where the cursor is. Speech-to-text is Deepgram; optional AI cleanup uses Groq. Meeting transcription runs on your own computer with Whisper.

Windows only. You bring your own API keys; nothing is sent anywhere except the Deepgram and Groq calls described below.

## Use it
- Open **dictation-tool** from the Start Menu or the Desktop shortcut. It lives in the system tray; closing the window keeps it running.
- It is a standalone program at `%LOCALAPPDATA%\Programs\dictation-tool\dictation-tool.exe`. It needs no Python.
- Keys go in Settings → Account. Deepgram (speech) is required: https://console.deepgram.com. A Groq key is optional and only used for the AI features on dictation: https://console.groq.com/keys

## What uses AI and what doesn't
| Part | Deepgram speech-to-text | Groq language model |
|---|---|---|
| Dictation | Yes | Only if Auto cleanup is Light or Medium and a Groq key is saved (dictations over six words) |
| Meetings | No by default: transcribed on this computer, free | Never |
| Command Mode, Transforms | Yes (to hear the instruction) | Yes |
| Dictionary, Snippets, Styles, Scratchpad, Insights | No | No |

Meetings are plain transcription that costs nothing: each stretch of speech is transcribed on this computer by a Whisper speech model (in `%LOCALAPPDATA%\dictation-tool\models`), so no credit or tokens are used and no audio leaves the PC. Pieces are labelled "Me" (microphone) or "Others" (the computer's audio). No speaker separation, no summaries. Settings → Notetaker can switch meetings back to Deepgram (paid per minute) or pick the faster "base" model. For notes, use "Copy transcript" and paste it to Claude.

## Default shortcuts
| Action | Shortcut |
|---|---|
| Push to talk (hold) | Caps Lock (new installs also get Ctrl + Win) |
| Hands-free (press to start, press to stop) | Ctrl + Win + Space, double-tap push to talk, or click the bar |
| Command Mode (hold) | Ctrl + Win + Alt |
| Paste / copy last transcript | Alt + Shift + Z / Alt + Shift + X |
| Cancel | Esc |
| Scratchpad | Win + Alt + S |
| Polish / Prompt Engineer / see changes | Win + Alt + 1 / Win + Alt + 2 / Win + Alt + O |

Change them in Settings → Shortcuts → Change. While Caps Lock is a shortcut, the app switches Caps Lock back off within a second if it ever ends up on.

## What's in it
| Area | What it does |
|---|---|
| Bar | Animated glowing pill that shows only while dictating: live waveform, gold in Command Mode, green check when text lands. Meme mode (Settings → System) makes it spin, bounce and flash |
| Clipboard | Pasting dictation saves everything on your clipboard first (text, images, files, rich text) and puts it back afterwards. If you copy something new in that moment, it is left alone. Nothing to paste into? The dictation is in History and on Alt + Shift + Z |
| Cleanup without AI | Removes um/uh, "scratch that", repeated words, set-off fillers ("it was, like, big", "So, ..."), and spoken take-backs: "Tuesday, no wait, Wednesday" becomes "Wednesday". Words like "like" and "so" are only removed when set off by commas |
| Home | History by day with search, copy, retry, undo AI edit, delete |
| Meetings | Starts on its own when Teams, Zoom or Webex uses the mic, or from the Meetings page or tray. Skips silence. Copy or export the transcript |
| Dictionary | Words as speech hints, corrections for misspellings, CSV import |
| Snippets | Spoken trigger → saved text, JSON import |
| Style | Formal, Casual, Very casual, Excited per app type; Auto cleanup None, Light, Medium |
| Transforms | Polish, Prompt Engineer and custom rewrites of selected text |
| Scratchpad | Notes window with tabs, pin, search, version history |
| Insights | Words per minute, fixes, total words, estimated Deepgram spend (also shown on Home), usage by app type, heatmap and streaks. Spend is audio sent × Deepgram's listed prices (`PRICES` in `dt_core.py`); the app can't read the real bill |

## Not built
Accounts, sync between devices, teams and admin controls, billing, mobile apps, calendar connection, voice profile, dictionary auto-learn, rich text and images, reading the text around the cursor.

## Where things live
- Code: this folder. `dictation_tool.pyw` starts it, `dt_core.py` is the logic, `dt_ui.py` the windows, `dt_bar.py` the animated bar, `make_logo.py` draws the logo.
- Installed program: `%LOCALAPPDATA%\Programs\dictation-tool`.
- Settings, history, saved audio (14 days), meetings, notes, stats, log: `%APPDATA%\dictation-tool`. Not in the vault, so nothing syncs between computers.
- API keys: Windows Credential Manager.

## After changing the code, or on another computer
Rebuild and reinstall (needs `uv`), from this folder:
```
powershell -ExecutionPolicy Bypass -File build.ps1
```
Or copy the `%LOCALAPPDATA%\Programs\dictation-tool` folder to the other PC and run `dictation-tool.exe`; that needs nothing installed.

## Check it works
Run the installed program's self-test (results go to the file you name):
```
"%LOCALAPPDATA%\Programs\dictation-tool\dictation-tool.exe" --selftest --out "%TEMP%\dictation-tool-selftest.txt"
```
