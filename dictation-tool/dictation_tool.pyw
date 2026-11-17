"""dictation-tool - AI dictation for Windows, powered by Deepgram.

Hold your shortcut, speak, release: the text lands where your cursor is.
Logic lives in dt_core.py, the interface in dt_ui.py. User data lives in
%APPDATA%\\dictation-tool; API keys live in Windows Credential Manager.

    dictation_tool.pyw               run the app
    dictation_tool.pyw --selftest    check rules, hook and microphone (use python.exe)
    dictation_tool.pyw --selftest --shots <folder>   also save a picture of each page
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk  # noqa: E402

import dt_core as c  # noqa: E402
import dt_ui  # noqa: E402


def run_selftest() -> int:
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))

    d = [{"word": "Acme-Corp", "misspelling": ""}, {"word": "Acme-Corp", "misspelling": "acmecorp"},
         {"word": "Bob", "misspelling": "John"}]
    s = [{"trigger": "sign off", "text": "Thanks,\nSam"}, {"trigger": "my address", "text": "123 Main St"}]
    t, n = c.apply_dictionary("call acmecorp about the acme corp job, John's idea. I don't know.", d)
    check("dictionary corrections", t == "call Acme-Corp about the Acme-Corp job, Bob's idea. I don't know." and n == 3, t)
    check("snippet alone", c.apply_snippets("Sign off.", s)[0] == "Thanks,\nSam")
    check("snippet inline", c.apply_snippets("send it to my address please", s)[0] == "send it to 123 Main St please")
    check("line commands", c.line_commands("Hi team. New line. see you. New paragraph bye") == "Hi team.\nSee you.\n\nBye",
          repr(c.line_commands("Hi team. New line. see you. New paragraph bye")))
    b = c.basic_cleanup("Um, let's meet at 2. Scratch that, at 3 uh tomorrow.")
    check("basic cleanup and backtrack", b == "At 3 tomorrow.", b)
    b = c.basic_cleanup("Meet on Tuesday, no wait, Wednesday. It was, like, big, you know, and I like it, so, yes.")
    check("take-backs and set-off fillers", b == "Meet on Wednesday. It was big and I like it, yes.", b)
    check("real words survive", c.basic_cleanup("I mean it. If so, we go. It is so good.") == "I mean it. If so, we go. It is so good.")
    check("style: casual drops the final period", c.apply_style("Thanks, see you.", "Casual", True, False) == "Thanks, see you")
    check("style: formal keeps it", c.apply_style("Thanks, see you.", "Formal", True, False) == "Thanks, see you.")
    check("style: very casual", c.apply_style("Thanks, See you.", "Very casual", True, False) == "thanks, see you")
    check("style: excited", c.apply_style("Thanks.", "Excited", False, False) == "Thanks!")
    check("press enter", c.split_press_enter("Send it now. Press enter.") == ("Send it now.", True))
    sc = c.DEFAULT_SHORTCUTS
    check("shortcut: letter alone rejected", c.validate_binding("ptt", [0x41], sc) is not None)
    check("shortcut: Caps Lock alone ok for push to talk", c.validate_binding("ptt", [c.VK_CAPS], sc, skip=("ptt", 1)) is None)
    check("shortcut: Ctrl+C reserved", "not allowed" in (c.validate_binding("ptt", [c.VK_CTRL, 0x43], sc) or ""))
    check("shortcut: duplicate caught", "already in use" in (c.validate_binding("command", [c.VK_CTRL, c.VK_WIN, 0x20], sc) or ""))
    check("shortcut: four keys rejected", "3 or fewer" in (c.validate_binding("ptt", [0x11, 0x12, 0x10, 0x41], sc) or ""))
    check("app category", c.classify_app("slack", "")["style_cat"] == "work"
          and c.classify_app("chrome", "Inbox - Gmail")["style_cat"] == "email"
          and c.classify_app("windowsterminal", "")["raw_only"])
    old = {"hotkey": [0x14], "mode": "Hold", "language": "en", "model": "nova-3",
           "dictionary": [{"term": "Acme-Corp", "sounds_like": "acmecorp, acme corp"}],
           "shortcuts": [{"trigger": "sign off", "text": "Thanks,\n"}]}
    m = c._migrate_v1(old)
    check("old settings migrate", m["shortcuts"]["ptt"] == [[0x14]] and len(m["dictionary"]) == 3
          and m["snippets"][0]["trigger"] == "sign off")
    e, sk = c.parse_dictionary_csv('Acme Corp\nAkme Corp,Acme Corp\n"Smith, Jane",Jane Smith\na,b,c\n')
    check("dictionary import", len(e) == 3 and sk == 1 and e[2]["misspelling"] == "Smith, Jane")
    e, sk = c.parse_snippets_json('[{"name": "address", "text": "123 Main St"}, {"name": "", "text": "x"}]')
    check("snippet import", len(e) == 1 and sk == 1)
    q = c.dg_params({"dg_model": "nova-3", "languages": ["en"], "auto_detect": False, "dictionary": d}, True)
    check("deepgram request", "keyterm=Acme-Corp" in q and "language=en" in q and q.count("keyterm=Acme-Corp") == 1, q)
    res = c.process_dictation("um call acmecorp tomorrow. new line sign off", dict(c.DEFAULTS, dictionary=d, snippets=s), "",
                              c.classify_app("notepad", ""))
    check("full pipeline without AI key", res["text"] == "Call Acme-Corp tomorrow.\nThanks,\nSam", repr(res["text"]))
    check("INPUT struct size", ctypes.sizeof(c.INPUT) == 40)
    mt = {"title": "T", "start": 0, "end": 120, "summary": "", "segments": [
        {"t": 1, "who": "Me", "text": "Hi."}, {"t": 3, "who": "Me", "text": "Ready?"}, {"t": 65, "who": "Speaker 1", "text": "Yes."}]}
    check("meeting transcript format", c.meeting_text(mt) == "[00:01] Me\nHi. Ready?\n\n[01:05] Speaker 1\nYes.", repr(c.meeting_text(mt)))
    check("meeting detection runs", isinstance(c.meeting_app_on_mic(), str))
    st = c.new_stats()
    c.backfill_spend(st, [{"ts": 0, "secs": 600}], [{"start": 0, "end": 600, "sent_secs": 600},
                                                    {"start": 0, "end": 600, "engine": "local", "sent_secs": 600}],
                     {"languages": ["en"], "auto_detect": False, "dictionary": []})
    c.add_spend(st, "dictation", 60, 0.0048)
    want = 10 * 0.0048 + 10 * 0.0068 + 0.0048  # 10 min dictation, 10 min old-style meeting, free meeting ignored
    check("spend estimate adds up", abs(st["spend"]["usd"] - want) < 1e-9 and c.money(0.336) == "$0.34"
          and c.money(0.004) == "under $0.01", f"{st['spend']['usd']} vs {want}")
    import array as _arr
    got = []
    gate = c.VoiceGate(got.append, 0.012, hang=0.05, lead_chunks=2)
    quiet, loud = bytes(3200), _arr.array("h", [9000] * 1600).tobytes()
    for chunk in (quiet, quiet, quiet, loud, quiet):
        gate.feed(chunk)
    sent_now = len(got)  # two lead-in chunks, the loud one, and one of tail
    import time as _tm
    _tm.sleep(0.08)
    gate.feed(quiet)
    check("silence is not sent, speech keeps a lead-in and a tail", sent_now == 4 and len(got) == 4, f"{sent_now} {len(got)}")
    them_gate = c.VoiceGate(lambda b: None, 0.002)
    mic_got = []
    mic_gate = c.VoiceGate(mic_got.append, 0.006, hang=0.05, lead_chunks=1, other=them_gate)
    echo = _arr.array("h", [300, -300] * 800).tobytes()    # speakers heard faintly by the mic
    voice = _arr.array("h", [4000, -4000] * 800).tobytes()  # you, talking over them
    them_loud = _arr.array("h", [3000, -3000] * 2400).tobytes()
    for _ in range(12):
        them_gate.feed(them_loud)
        mic_gate.feed(echo)
    echo_sent = len(mic_got)
    them_gate.feed(them_loud)
    mic_gate.feed(voice)
    check("speaker echo on the mic is not sent, your own voice is", echo_sent == 0 and len(mic_got) == 2, f"{echo_sent} {len(mic_got)}")
    try:  # the free on-computer engine, on one of your saved dictation recordings (word count only)
        import glob
        import os
        import time as _lt
        import wave
        clips = sorted(glob.glob(os.path.join(str(c.AUDIO_DIR), "*.wav")), key=os.path.getmtime, reverse=True)
        clip = None
        for f in clips:
            with wave.open(f) as w:
                if 6 <= w.getnframes() / w.getframerate() <= 25:
                    clip = (w.readframes(w.getnframes()), w.getframerate())
                    break
        if clip:
            c.LOCAL.transcribe(clip[0][:16000], clip[1], c.DEFAULTS)  # first call loads the model
            t_loc = _lt.perf_counter()
            words = len(c.LOCAL.transcribe(clip[0], clip[1], c.DEFAULTS).split())
            took = _lt.perf_counter() - t_loc
            secs = len(clip[0]) / 2 / clip[1]
            check(f"free on-computer engine: {secs:.0f} s of speech -> {words} words in {took:.1f} s", words > 3)
        else:
            print("INFO  no saved recording to try the on-computer engine with")
    except Exception as ex:
        check("free on-computer engine", False, repr(ex))
    if "--live" in sys.argv:  # talks to Deepgram with the saved key: a second of silence, then a 5 second meeting
        import queue
        import time
        cfg = c.load_settings()
        key = c.get_key(cfg, "deepgram")
        try:
            out = c.transcribe_pcm(key, cfg, bytes(c.SAMPLE_RATE * 2))
            check("live: dictation request accepted by Deepgram", out == "", out)
        except Exception as ex:
            check("live: dictation request accepted by Deepgram", False, c.friendly_error(ex))
        dq: queue.Queue = queue.Queue()  # one dictation the way the app does it: stream 2 s, then release
        sess = c.Session(1, key, cfg, dq.put)
        sess.start()
        for _ in range(40):
            sess.feed(bytes(c.CHUNK * 2))
            time.sleep(0.05)
        t_rel = time.time()
        sess.finish()
        try:
            ev = dq.get(timeout=20)
            ms = int((time.time() - t_rel) * 1000)
            check(f"live: streamed dictation, release to transcript in {ms} ms", ev[0] == "done" and ev[3] is None, str(ev))
        except queue.Empty:
            check("live: streamed dictation returned", False, "no answer in 20 s")
        evq: queue.Queue = queue.Queue()
        rec = c.MeetingRecorder(key, cfg, evq.put, "test")
        rec.start()
        time.sleep(9)
        rec.stop()
        time.sleep(6)  # the last pieces come back after stopping
        got_mt = [e for e in list(evq.queue) if e[0] == 'mt']
        errs = [e for e in list(evq.queue) if e[0] == 'mt_error']
        print(f"INFO  9 s meeting: {rec.sent_seconds():.1f} s of audio sent (18 s if nothing were skipped), "
              f"{len(got_mt)} pieces of text back, computer audio {'on' if rec.system_audio else 'unavailable'}")
        check("live: plain meeting transcription runs without errors", not errs and rec.failed == 0, str(errs))
    import time as _clock
    t_start = _clock.perf_counter()
    app = dt_ui.App(selftest=True)
    app.update()  # let the first page finish drawing before the timed checks start
    print(f"INFO  app window built in {int((_clock.perf_counter() - t_start) * 1000)} ms")
    t_home = _clock.perf_counter()
    app.show("home")
    app.update()
    print(f"INFO  Home page redraw takes {int((_clock.perf_counter() - t_home) * 1000)} ms")
    sp = app.stats["spend"]
    print(f"INFO  spend shown: {c.money(sp['usd'])} total, dictation {sp['dictation_secs'] / 60:.1f} min, "
          f"meetings {sp.get('meeting_secs', 0) / 60:.1f} min")
    check("keyboard hook installed", bool(app.hook.khook))
    app.hook.set_bindings({"ptt": [[0x7C]], "handsfree": [[0x7C, 0x7D]], "cancel": [[0x1B]]})  # F13, F13+F14
    mic = {"bytes": 0, "err": None}
    shots = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else None

    def keys(*events):
        c.send_inputs([c._key(vk, flags=0 if down else 2) for vk, down in events])

    def mic_start():
        try:
            app.recorder.start(lambda b: mic.__setitem__("bytes", mic["bytes"] + len(b)), "Auto-detect")
        except Exception as ex:
            mic["err"] = str(ex)

    def shoot(i=0):
        pages = [k for k, _ in app.NAV] + ["settings", "help"]
        if shots is None or i >= len(pages):
            return finish()
        from PIL import ImageGrab
        app.show(pages[i])
        app.update()

        def grab():
            x, y = app.winfo_rootx(), app.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(shots / f"{i}-{pages[i]}.png")
            shoot(i + 1)

        app.after(500, grab)

    def finish():
        app.recorder.stop(False)
        want = [("sc_down", "ptt"), ("sc_down", "handsfree"), ("sc_up", "handsfree")]
        check("shortcut down, combo upgrade, up", app.selftest_events == want, str(app.selftest_events))
        check("microphone captures audio", mic["bytes"] > 0, mic["err"] or "0 bytes")
        if app.meetings:  # opening the longest saved meeting must be quick
            big = max(app.meetings, key=lambda m: len(m["segments"]))
            t_open = _t.perf_counter()
            app.open_meeting(big)
            app.update()
            ms_open = int((_t.perf_counter() - t_open) * 1000)
            check(f"longest meeting ({len(big['segments'])} pieces) opens in {ms_open} ms", ms_open < 4000)
            app.meeting_win.destroy()
        t_pad = _t.perf_counter()
        app.toggle_scratchpad(force_show=True)
        app.update()
        check(f"Scratchpad opens in {int((_t.perf_counter() - t_pad) * 1000)} ms", app.pad.winfo_viewable())
        app.pad.withdraw()
        check("all pages built without error", not page_errors, str(page_errors))
        app.quit_app()

    page_errors = []
    orig_exc = c.log.exception
    c.log.exception = lambda msg, *a, **k: (page_errors.append(msg % a if a else msg), orig_exc(msg, *a, **k))
    import math
    import time as _t
    bar = app.bar
    try:
        check("flow bar draws to the screen", bar._present(bar._render(_t.time(), "rest")))
    except Exception as ex:
        check("flow bar draws to the screen", False, repr(ex))
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        app.attributes("-topmost", True)
        import dt_bar
        from PIL import Image
        bar.hist.extend([abs(math.sin(i * 0.7)) * (0.35 + 0.65 * (i % 5) / 4) for i in range(24)])
        bar.lv = 0.7
        bar.sparks = [{"x": 180 + i * 14, "y": 52 - i * 4 % 30, "vx": 0, "vy": 0, "life": 0.4 + (i % 4) * 0.2, "rate": 1,
                       "r": 1.2 + (i % 3) * 0.6} for i in range(8)]
        app.rec = {"mode": "dictate", "t0": _t.time() - 7}
        kinds = ["rest", "hover", "rec", "locked", "proc", "ok", "err"]
        frames = [bar.preview(k) for k in kinds]
        app.rec["mode"] = "command"
        frames.append(bar.preview("rec"))
        sheet = Image.new("RGBA", (frames[0].width * 2, frames[0].height * 4), (40, 44, 52, 255))
        for i, fr in enumerate(frames):
            sheet.paste(fr, ((i % 2) * fr.width, (i // 2) * fr.height))
        sheet.save(shots / "bar-states.png")
        app.cfg["meme_mode"] = True  # not saved: the self-test never writes settings
        bar.reconfigure()
        bar.spin = 24
        bar.emoji = [{"x": 120 + i * 40, "y": 60 + (i * 53) % 150, "vx": 0, "vy": 0, "life": 1, "i": i % max(1, len(bar.sprites))}
                     for i in range(7)] if bar.sprites else []
        bar.preview("rec").save(shots / "bar-meme.png")
        app.cfg["meme_mode"] = False
        bar.emoji, bar.spin, bar.lift = [], 0.0, 0.0
        bar.reconfigure()
        app.rec, bar.sparks, bar.lv = None, [], 0.0
        bar.w, bar.h = dt_bar.SIZES["rest"]
    app.after(400, lambda: keys((0x7C, True)))
    app.after(550, lambda: keys((0x7D, True)))
    app.after(700, lambda: keys((0x7D, False), (0x7C, False)))
    app.after(800, mic_start)
    app.after(900, lambda: app.bar.notify("Self-test", "Checking the microphone and each page.", timeout=1500))
    app.after(1500, shoot if shots else lambda: [app.show(p) for p in [k for k, _ in app.NAV] + ["settings", "help"]] and None)
    if not shots:
        app.after(2600, finish)
    app.mainloop()
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


def main() -> int:
    if "--out" in sys.argv:  # the packaged app has no console: write self-test results to a file instead
        sys.stdout = sys.stderr = open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8", buffering=1)
    c.set_app_id()
    if "--selftest" in sys.argv:
        return run_selftest()
    mutex = c.kernel32.CreateMutexW(None, False, "dictation-tool-single-instance")
    if ctypes.get_last_error() == 183:  # already running
        c.user32.MessageBoxW(None, "Dictation Tool is already running. Look for it in the system tray.", c.APP, 0x40)
        return 0
    try:
        dt_ui.App().mainloop()
    except Exception:
        c.log.exception("fatal")
        raise
    finally:
        del mutex
    return 0


if __name__ == "__main__":
    sys.exit(main())
