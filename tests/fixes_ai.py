"""Regression tests for the AI-generation and background-service fixes in
docs/BUG_REPORT.md.

    python tests/fixes_ai.py

  H14  status checks are cheap and bounded; the Ollama client has real timeouts;
       Cancel works during a stalled model load
  H15  num_ctx is sized to the prompt; very long meetings are reduced
       hierarchically
  M25  filler removal only drops true disfluencies
  M26  the chunker splits on Arabic/CJK punctuation and newlines, never mid-word,
       and overlaps unpunctuated text
  M27  cloud generation retries 429/5xx/timeouts (Retry-After, capped) with
       friendly error messages
  M28  the single-instance lock is per session and reports errors distinctly
  M29  an unfetchable/unusable checksum asset makes the update unverifiable
  L7   a pasted GitHub URL is normalised to owner/name for update checks

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported). No real network, Ollama or GitHub: all servers are local fakes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_fixes_ai_"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.pop("MICO360_CONNECT_API_KEY", None)
ROOT = Path(os.environ.get("MICO360_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PySide6.QtWidgets import QApplication              # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                              # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


# -- a scriptable local HTTP server ---------------------------------------------
class FakeServer:
    """Each request pops the next action from ``script`` (the last one repeats):
       ("json", payload) | ("status", code, headers, body) | ("stall", seconds)
       | ("drop",)"""

    def __init__(self, script):
        self.script = list(script)
        self.hits = 0
        self.paths: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                with lock:
                    outer.hits += 1
                    outer.paths.append(self.path)
                    act = outer.script.pop(0) if len(outer.script) > 1 else outer.script[0]
                kind = act[0]
                if kind == "stall":
                    time.sleep(act[1])
                    self.close_connection = True
                    return
                if kind == "drop":
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                if kind == "json":
                    code, headers, body = 200, {}, json.dumps(act[1])
                else:
                    code, headers, body = act[1], act[2], act[3]
                data = body.encode("utf-8")
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _handle

        lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def timed(fn):
    t0 = time.monotonic()
    try:
        return fn(), time.monotonic() - t0, None
    except BaseException as exc:          # noqa: BLE001 — reported by the caller
        return None, time.monotonic() - t0, exc


_CHAT_OK = {"choices": [{"message": {"role": "assistant", "content": "Minutes OK."}}]}


# =============================================================================
def test_h14_status_and_timeouts() -> None:
    print("H14 — bounded, cached status checks; real Ollama timeouts; cancel works")
    from mico360.core import cloud_client as cc, ollama_client as oc

    c = oc._client("http://127.0.0.1:9")
    t = c._client.timeout
    check("generation Ollama client has a connect timeout (~3 s) and a long read timeout",
          t.connect == oc.GEN_CONNECT_TIMEOUT == 3.0 and t.read == oc.GEN_READ_TIMEOUT
          and t.read >= 300, f"{t}")

    stall = FakeServer([("stall", 8)])
    try:
        oc.invalidate_status_cache()
        st, dt, err = timed(lambda: oc.check_status(stall.url))
        check("Ollama status against a stalled host returns 'not running' within ~3 s",
              err is None and st is not None and not st.running and dt < 4.0,
              f"dt={dt:.2f} err={err!r} st={st}")
        hits = stall.hits
        st2, dt2, _ = timed(lambda: oc.check_status(stall.url))
        check("a failed status is cached briefly (the next call is instant, no request)",
              dt2 < 0.2 and not st2.running and stall.hits == hits, f"dt={dt2:.2f}")
        # cancel during a stalled model load
        gen = oc.OllamaGenerator(stall.url, "llama3.1")
        flag = {"t0": time.monotonic()}
        _, dtc, errc = timed(lambda: gen._chat(
            "hello", cancel=lambda: time.monotonic() - flag["t0"] > 0.4))
        check("Cancel interrupts a generation while Ollama sends nothing (stalled load)",
              isinstance(errc, InterruptedError) and dtc < 2.0, f"dt={dtc:.2f} err={errc!r}")

        cc.invalidate_status_cache()
        st, dt, err = timed(lambda: cc.check_cloud_status(stall.url + "/v1", key="k"))
        check("cloud status against a stalled server returns within ~4 s",
              err is None and not st.running and dt < 5.0, f"dt={dt:.2f} err={err!r}")
        check("cloud status error is friendly (not a bare 'timed out')",
              st.error.startswith("Could not reach MICO360 Connect") and st.error != "timed out",
              st.error)
        st2, dt2, _ = timed(lambda: cc.check_cloud_status(stall.url + "/v1", key="k"))
        check("cloud failure is cached briefly", dt2 < 0.2 and not st2.running, f"dt={dt2:.2f}")
    finally:
        stall.close()

    tags = {"models": [
        {"name": "llama3.1:latest", "model": "llama3.1:latest", "size": 1,
         "details": {"family": "llama", "families": ["llama"]}},
        {"name": "llava:7b", "model": "llava:7b", "size": 1,
         "details": {"family": "llama", "families": ["llama", "clip"]}},
    ]}
    ok = FakeServer([("json", tags)])
    try:
        oc.invalidate_status_cache()
        st = oc.check_status(ok.url)
        check("Ollama status lists usable text models (vision filtered)",
              st.running and st.models == ["llama3.1:latest"], f"{st}")
        oc.check_status(ok.url)
        check("a successful status is cached (no second request)", ok.hits == 1, f"hits={ok.hits}")
        oc.check_status(ok.url, use_cache=False)
        check("use_cache=False forces a fresh check", ok.hits == 2, f"hits={ok.hits}")
        # AppContext goes through the same cache
        from mico360.ui.context import AppContext
        ctx = AppContext()
        ctx.settings.set("ai_provider", "local")
        ctx.settings.set("ollama_host", ok.url)
        s1 = ctx.ai_status(); s2 = ctx.ai_status(); s3 = ctx.ollama_status()
        check("AppContext.ai_status/ollama_status reuse the cached status",
              s1.running and s2 is s1 and s3 is s1 and ok.hits == 2, f"hits={ok.hits}")
        ctx.invalidate_ai_status()
        ctx.ai_status()
        check("AppContext.invalidate_ai_status forces a refresh", ok.hits == 3, f"hits={ok.hits}")
        ctx.ai_status(force=True)
        check("ai_status(force=True) bypasses the cache", ok.hits == 4, f"hits={ok.hits}")
        ctx.close()
        # pulling a model invalidates the cache
        oc.check_status(ok.url)
        class _PullClient:
            def pull(self, model, stream=True):
                yield {"status": "success"}
        real = oc._client
        oc._client = lambda host, timeout=None: _PullClient()
        try:
            oc.pull_model(ok.url, "x")
        finally:
            oc._client = real
        h = ok.hits
        oc.check_status(ok.url)
        check("installing a model invalidates the cached model list", ok.hits == h + 1)
    finally:
        ok.close()

    models = FakeServer([("json", {"data": [{"id": "b"}, {"id": "a"}]})])
    try:
        cc.invalidate_status_cache()
        st = cc.check_cloud_status(models.url + "/v1", key="k1")
        cc.check_cloud_status(models.url + "/v1", key="k1")
        check("cloud success is cached (one request for two checks)",
              st.running and st.models == ["a", "b"] and models.hits == 1, f"hits={models.hits}")
        cc.check_cloud_status(models.url + "/v1", key="k2")
        check("a different API key is not served from another key's cache", models.hits == 2)
    finally:
        models.close()


# =============================================================================
class _FakeOllama:
    """Stands in for ollama.Client: captures chat() options and returns notes."""
    calls: list[dict] = []

    def __init__(self, reply=lambda prompt: "note " * 50):
        self.reply = reply

    def chat(self, model, messages, stream, options):
        prompt = messages[0]["content"]
        _FakeOllama.calls.append({"prompt": prompt, "options": dict(options)})
        text = self.reply(prompt)
        return iter([{"message": {"content": text[:len(text) // 2]}},
                     {"message": {"content": text[len(text) // 2:]}}])


def test_h15_context_and_hierarchical_reduce() -> None:
    print("H15 — num_ctx sized to the prompt; hierarchical reduce for long meetings")
    from mico360.core import generation as G, ollama_client as oc, prompts as P

    check("a short prompt gets the minimum context (4096)", oc.num_ctx_for("hi") == 4096)
    n20 = oc.num_ctx_for("x" * 20000)
    check("a 20k-char prompt gets a context that holds it plus the answer",
          n20 >= -(-20000 // 3.5) + oc.RESPONSE_TOKENS and n20 == 8192, f"{n20}")
    check("context is clamped to 32768 for huge prompts", oc.num_ctx_for("x" * 500000) == 32768)

    real = oc._client
    _FakeOllama.calls = []
    oc._client = lambda host, timeout=None: _FakeOllama()
    try:
        gen = oc.OllamaGenerator("http://fake", "llama3.1")
        out = gen._chat("y" * 30000)
        opts = _FakeOllama.calls[-1]["options"]
        check("Ollama chat passes num_ctx (sized to the prompt) and temperature",
              opts.get("num_ctx") == oc.num_ctx_for("y" * 30000) and opts["num_ctx"] >= 30000 / 3.5
              and opts.get("temperature") == 0.2 and out.startswith("note"), f"{opts}")

        # a ~2.5 hour meeting through the whole local pipeline
        sentences = [f"Speaker {i % 4}: item {i} was discussed and agreed by the team." for i in range(3500)]
        transcript = " ".join(sentences)
        _FakeOllama.calls = []
        tpl = "BEFORE-MARKER write a TL;DR.\n\n" + P.TRANSCRIPT_TOKEN + "\n\nAFTER-MARKER in Arabic."
        oc._client = lambda host, timeout=None: _FakeOllama(lambda p: "fact " * 400)  # ~2000 chars
        result = gen.generate_minutes(transcript, tpl, "Short Summary")
        calls = _FakeOllama.calls
        merges = [c for c in calls if "condensing notes from a long meeting" in c["prompt"]]
        final = calls[-1]["prompt"]
        check("every Ollama request carries a num_ctx large enough for its prompt",
              all(c["options"]["num_ctx"] >= len(c["prompt"]) / 3.5 for c in calls)
              and all(4096 <= c["options"]["num_ctx"] <= 32768 for c in calls))
        check("long meeting: notes are merged in groups before the final reduce",
              len(merges) >= 2, f"merges={len(merges)} calls={len(calls)}")
        notes_part = final.split(P.REDUCE_NOTES_PREFIX, 1)[-1]
        check("final reduce prompt stays within the notes budget",
              len(notes_part) <= G.REDUCE_NOTES_BUDGET + 600, f"{len(notes_part)}")
        check("final reduce keeps the whole user template (text before AND after the token)",
              "BEFORE-MARKER" in final and "AFTER-MARKER in Arabic" in final
              and P.REDUCE_NOTES_PREFIX in final and result.startswith("fact"))
        labels = re.findall(r"### Parts (\d+)–(\d+)", notes_part)
        n_chunks = len([c for c in calls if "Transcript part" in c["prompt"]])
        check("merged notes cover every part, in order (start of the meeting kept)",
              labels and labels[0][0] == "1" and labels[-1][1] == str(n_chunks)
              and [int(a) for a, _ in labels] == sorted(int(a) for a, _ in labels),
              f"{labels[:3]}…{labels[-2:]} n={n_chunks}")

        # a moderately long meeting is NOT merged (single reduce, unchanged behaviour)
        _FakeOllama.calls = []
        oc._client = lambda host, timeout=None: _FakeOllama(lambda p: "fact " * 60)
        gen.generate_minutes(" ".join(sentences[:500]), P.BASE_TEMPLATE, "Formal Minutes")
        check("a meeting whose notes fit the budget uses one reduce and no merges",
              not any("condensing notes" in c["prompt"] for c in _FakeOllama.calls)
              and P.REDUCE_NOTES_PREFIX in _FakeOllama.calls[-1]["prompt"])
    finally:
        oc._client = real

    # cancel between parts is honoured
    n = {"c": 0}

    def chat(p, cancel):
        n["c"] += 1
        return "x"
    _, _, err = timed(lambda: G.run_minutes_pipeline(
        chat, "m", "Word. " * 5000, P.BASE_TEMPLATE, cancel=lambda: n["c"] >= 2))
    check("pipeline stops at the next step after Cancel", isinstance(err, InterruptedError)
          and n["c"] == 2, f"calls={n['c']} err={err!r}")


# =============================================================================
def test_m25_fillers() -> None:
    print("M25 — filler removal keeps meaning")
    from mico360.core import cleaning as C
    clean = lambda s: C.clean_transcript(s, True)          # noqa: E731
    cases = {
        "Do you know if the budget is approved?": "Do you know if the budget is approved?",
        "What kind of contract did we sign?": "What kind of contract did we sign?",
        "I think that that plan works.": "I think that that plan works.",
        "I mean, it had had problems, sort of.": "I mean, it had had problems, sort of.",
        "Mm-hmm, like I said, actually it is basically done, right?":
            "Mm-hmm, like I said, actually it is basically done, right?",
        "The umbrella budget, uh-oh, Hummus.": "The umbrella budget, uh-oh, Hummus.",
    }
    for src, want in cases.items():
        got = clean(src)
        check(f"kept: {src[:38]!r}", got == want, repr(got))
    got = clean("um the the meeting uh started")
    check("'um the the meeting uh started' -> no filler, no stutter",
          got == "The meeting started" and "the the" not in got.lower(), repr(got))
    got = clean("We, uh, should, um, approve it. Hmm. Next item.")
    check("fillers wrapped in commas / standalone go cleanly",
          got == "We should approve it. Next item.", repr(got))
    got = clean("I I think we we should go")
    check("function-word stutters collapse", got == "I think we should go", repr(got))
    check("every filler entry actually matches (no dead punctuated entries)",
          all(clean(f"we {f} go") == "we go" for f in C.FILLERS)
          and all(re.fullmatch(r"[a-z]+", f) for f in C.FILLERS))
    ar = "هل تعرف إذا تمت الموافقة على الميزانية؟ نعم نعم"
    check("Arabic text is untouched", clean(ar) == ar, repr(clean(ar)))
    check("disabled filler removal leaves text as is",
          C.clean_transcript("um the the", False) == "um the the")


# =============================================================================
def test_m26_chunking() -> None:
    print("M26 — chunking: Arabic/CJK punctuation, newlines, never mid-word, overlap")
    from mico360.core import cleaning as C
    words_ar = ["الاجتماع", "الميزانية", "المشروع", "الفريق", "التسليم", "العميل",
                "الموعد", "النهائي", "المراجعة", "الخطة"]
    sents = [" ".join(words_ar[(i + j) % 10] for j in range(12 + i % 7)) + "؟" for i in range(400)]
    ar = " ".join(sents)
    ch = C.chunk_text(ar, max_chars=2000, overlap_chars=300)
    check("Arabic without Latin punctuation: chunks respect max_chars",
          len(ch) > 5 and all(len(c) <= 2000 for c in ch), f"n={len(ch)}")
    check("Arabic: chunks end on '؟' sentence boundaries",
          all(c.endswith("؟") for c in ch), repr([c[-5:] for c in ch if not c.endswith("؟")][:3]))
    check("Arabic: consecutive chunks overlap",
          all(b.split("؟")[0] in a for a, b in zip(ch, ch[1:])))

    lines = [f"[00:{i % 60:02d}] Speaker {i % 3} talks about topic {i} without punctuation"
             for i in range(600)]
    txt = "\n".join(lines)
    ch = C.chunk_text(txt, max_chars=1500, overlap_chars=200)
    all_lines = set(lines)
    check("newline-separated turns: chunks contain whole lines (and keep line breaks)",
          all(all(l in all_lines for l in c.split("\n")) for c in ch) and all("\n" in c for c in ch))

    vocab = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
             "india", "juliet", "kilo", "lima"]
    raw = " ".join(vocab[i % 12] + str(i) for i in range(4000))    # no punctuation at all
    ch = C.chunk_text(raw, max_chars=1000, overlap_chars=200)
    valid = set(raw.split())
    check("unpunctuated text: every chunk <= max_chars", all(len(c) <= 1000 for c in ch))
    check("unpunctuated text: never split mid-word",
          all(all(w in valid for w in c.split()) for c in ch))
    check("unpunctuated text: consecutive chunks overlap (and never by more than overlap)",
          all(" ".join(b.split()[:2]) in a for a, b in zip(ch, ch[1:])))
    seen = set()
    for c in ch:
        seen.update(c.split())
    check("unpunctuated text: nothing is lost", seen == valid, f"{len(valid - seen)} missing")
    check("no chunk consists only of the previous chunk's overlap",
          all(b not in a for a, b in zip(ch, ch[1:])))

    cjk = "会议于上午十点开始讨论预算问题。" * 300
    ch = C.chunk_text(cjk, max_chars=500, overlap_chars=60)
    check("CJK '。' (no spaces) is a boundary", all(len(c) <= 500 and c.endswith("。") for c in ch))
    ch = C.chunk_text("A. " * 4000, max_chars=2000, overlap_chars=200)
    check("module_tests case still gives several overlapping chunks",
          len(ch) > 1 and all(len(c) <= 2000 for c in ch))


# =============================================================================
def test_m27_cloud_retries() -> None:
    print("M27 — cloud retries with backoff and friendly errors")
    from mico360.core import cloud_client as cc
    saved = {k: getattr(cc, k) for k in ("BACKOFF_BASE", "MAX_TOTAL_WAIT", "MAX_SINGLE_WAIT",
                                         "MAX_ATTEMPTS", "_TIMEOUT")}

    def gen(srv):
        return cc.CloudGenerator(base_url=srv.url + "/v1", key="k", model="m")

    try:
        cc.BACKOFF_BASE = 0.2
        s = FakeServer([("status", 503, {"Retry-After": "1"}, "{}"),
                        ("status", 503, {"Retry-After": "1"}, "{}"), ("json", _CHAT_OK)])
        out, dt, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("503 + Retry-After is retried and honoured, then succeeds",
              out == "Minutes OK." and s.hits == 3 and 1.8 <= dt < 5, f"dt={dt:.2f} err={err!r}")

        s = FakeServer([("status", 429, {}, '{"error":{"message":"slow down"}}'), ("json", _CHAT_OK)])
        out, dt, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("429 without Retry-After uses exponential backoff, then succeeds",
              out == "Minutes OK." and s.hits == 2 and dt < 2, f"dt={dt:.2f} err={err!r}")

        s = FakeServer([("drop",), ("json", _CHAT_OK)])
        out, dt, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("a dropped connection is retried", out == "Minutes OK." and s.hits == 2, f"err={err!r}")

        s = FakeServer([("status", 400, {}, '{"error":{"message":"bad request"}}')])
        _, _, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("a 400 is not retried and shows the server message",
              isinstance(err, RuntimeError) and s.hits == 1 and "bad request" in str(err), f"{err!r}")

        cc.MAX_SINGLE_WAIT, cc.MAX_TOTAL_WAIT = 0.5, 1.0
        s = FakeServer([("status", 503, {"Retry-After": "3600"}, "{}")])
        _, dt, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("a huge Retry-After is capped and the total wait is bounded",
              isinstance(err, RuntimeError) and dt < 3 and "503" in str(err)
              and "tried" in str(err), f"dt={dt:.2f} {err!r}")

        cc.MAX_SINGLE_WAIT, cc.MAX_TOTAL_WAIT = 30.0, 60.0
        s = FakeServer([("status", 503, {"Retry-After": "20"}, "{}")])
        t0 = time.monotonic()
        _, dt, err = timed(lambda: gen(s)._chat("hi", cancel=lambda: time.monotonic() - t0 > 0.5))
        s.close()
        check("Cancel is honoured while waiting to retry", isinstance(err, InterruptedError)
              and dt < 2, f"dt={dt:.2f} {err!r}")

        cc._TIMEOUT, cc.MAX_ATTEMPTS, cc.BACKOFF_BASE = 0.5, 2, 0.1
        s = FakeServer([("stall", 3)])
        _, dt, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        msg = str(err)
        check("a read timeout is retried, then reported in plain words",
              isinstance(err, RuntimeError) and s.hits == 2 and "did not respond in time" in msg
              and msg.strip().lower() != "timed out", f"hits={s.hits} {err!r}")

        cc.MAX_ATTEMPTS = 1
        s = FakeServer([("drop",)])
        _, _, err = timed(lambda: gen(s)._chat("hi"))
        s.close()
        check("a dropped connection maps to a friendly message",
              isinstance(err, RuntimeError) and "MICO360 Connect" in str(err)
              and ("closed the connection" in str(err) or "interrupted" in str(err)), f"{err!r}")
        cc._TIMEOUT = 15.0            # Windows retries a refused SYN for ~2 s
        port = FakeServer([("drop",)])
        dead = port.url
        port.close()
        _, _, err = timed(lambda: cc.CloudGenerator(base_url=dead + "/v1", key="k")._chat("hi"))
        check("connection refused maps to a friendly message",
              isinstance(err, RuntimeError) and "Could not reach MICO360 Connect" in str(err), f"{err!r}")
    finally:
        for k, v in saved.items():
            setattr(cc, k, v)


# =============================================================================
def test_m28_single_instance() -> None:
    print("M28 — per-session single-instance lock")
    from mico360 import single_instance as si
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
             "from mico360 import single_instance as s; r = s.acquire(); "
             "print('ACQ', r, repr(s.last_error())); s.release()")

    def other_process() -> str:
        r = subprocess.run([sys.executable, "-c", probe, str(ROOT)], capture_output=True,
                           text=True, timeout=60, env=dict(os.environ))
        m = re.search(r"ACQ (\w+)", r.stdout)
        return m.group(1) if m else f"?{r.stdout}{r.stderr}"

    first = si.acquire()
    again = si.acquire()
    check("first acquire succeeds; re-acquiring in the same process is harmless",
          first is True and again is True and si.last_error() == "")
    check("a second process in the same session is told an instance is running",
          other_process() == "False")
    si.release()
    check("after release a new process can start", other_process() == "True")
    if sys.platform == "win32":
        check("Windows lock is a session-local named mutex (not a machine-wide port)",
              si.MUTEX_NAME.startswith("Local\\") and not hasattr(si, "_LOCK_PORT"))
        # the old loopback port being busy must not matter any more
        blocker = socket.socket()
        try:
            blocker.bind(("127.0.0.1", 47917))
            blocker.listen(1)
            ok = si.acquire()
        except OSError:
            ok = si.acquire()
        finally:
            blocker.close()
        check("an unrelated program on the old port does not block start-up", ok is True)
        si.release()
    real = si._acquire_windows if sys.platform == "win32" else si._acquire_lockfile

    def boom():
        raise OSError(1450, "Insufficient system resources")
    if sys.platform == "win32":
        si._acquire_windows = boom
    else:
        si._acquire_lockfile = boom
    try:
        ok = si.acquire()
        check("a lock failure other than 'already running' is reported distinctly "
              "(start allowed, error recorded)", ok is True and "Insufficient" in si.last_error(),
              si.last_error())
    finally:
        if sys.platform == "win32":
            si._acquire_windows = real
        else:
            si._acquire_lockfile = real
        si.release()


# =============================================================================
def test_m29_checksum() -> None:
    print("M29 — an unusable checksum asset blocks the install")
    from mico360.core import updater as up
    good = "c" * 64
    url = "https://github.com/o/r/releases/download/v9/MICO360Meetings-Setup-9.0.0.exe"
    real_fetch = up.fetch_text

    def fail(u, timeout=8.0):
        raise OSError("network down")
    try:
        up.fetch_text = fail
        info = up.UpdateInfo(download_url=url, checksum_url="https://x/SHA256SUMS.txt")
        try:
            up.resolve_expected_sha256(info)
            raised = None
        except up.IntegrityError as exc:
            raised = exc
        check("checksum asset can't be fetched -> IntegrityError (not silently unverified)",
              raised is not None and "can't be verified" in str(raised), repr(raised))

        up.fetch_text = lambda u, timeout=8.0: f"{'d' * 64}  SomeOtherFile.exe\n"
        try:
            up.resolve_expected_sha256(info)
            raised = None
        except up.IntegrityError as exc:
            raised = exc
        check("checksum asset without this installer's hash -> IntegrityError", raised is not None)

        up.fetch_text = fail
        info2 = up.UpdateInfo(download_url=url, checksum_url="https://x/SHA256SUMS.txt",
                              expected_sha256=good)
        check("…unless the release notes name this installer's hash (then it is enforced)",
              up.resolve_expected_sha256(info2) == good)
        check("a release that publishes no checksum at all still resolves to ''",
              up.resolve_expected_sha256(up.UpdateInfo(download_url=url)) == "")

        # the download worker deletes the file and reports failure
        from mico360.ui import workers as W
        dest = _TMP / "MICO360Meetings-Setup-9.0.0.exe"
        real_dl = up.download_asset

        def fake_dl(u, d, progress=None, cancel=None):
            Path(d).write_bytes(b"installer")
            return d
        up.download_asset = fake_dl
        try:
            w = W.UpdateDownloadWorker(info, str(dest))
            got = {"ok": [], "fail": []}
            w.finished_ok.connect(got["ok"].append)
            w.failed.connect(got["fail"].append)
            w.run()
        finally:
            up.download_asset = real_dl
        check("download worker refuses the unverifiable installer and deletes it",
              not got["ok"] and got["fail"] and not dest.exists(), f"{got}")
    finally:
        up.fetch_text = real_fetch


# =============================================================================
def test_l7_repo_normalisation() -> None:
    print("L7 — pasted GitHub URLs are normalised to owner/name")
    from mico360.core import updater as up
    cases = {
        "owner/name": "owner/name",
        " https://github.com/owner/name ": "owner/name",
        "https://github.com/owner/name.git": "owner/name",
        "https://www.github.com/owner/name/releases/latest": "owner/name",
        "github.com/owner/name/": "owner/name",
        "git@github.com:owner/name.git": "owner/name",
        "http://github.com/Owner-1/my.repo?tab=readme": "Owner-1/my.repo",
        "": "",
    }
    for src, want in cases.items():
        check(f"normalize_repo({src!r})", up.normalize_repo(src) == want, up.normalize_repo(src))
    check("repo_url accepts a full URL",
          up.repo_url("https://github.com/owner/name.git") == "https://github.com/owner/name")
    seen = []
    import urllib.error
    import urllib.request as UR
    real = UR.urlopen

    def fake(req, timeout=0):
        seen.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    UR.urlopen = fake
    try:
        info = up.check_for_updates("https://github.com/owner/name.git")
    finally:
        UR.urlopen = real
    check("check_for_updates queries owner/name for a pasted URL",
          seen == ["https://api.github.com/repos/owner/name/releases/latest"], f"{seen}")
    check("…and its message names the normalised repo", "(owner/name)" in info.error, info.error)
    check("a non-repo value is still 'not configured'",
          up.check_for_updates("just-a-name").status == up.NOT_CONFIGURED)


# =============================================================================
def main() -> int:
    for fn in (test_h14_status_and_timeouts, test_h15_context_and_hierarchical_reduce,
               test_m25_fillers, test_m26_chunking, test_m27_cloud_retries,
               test_m28_single_instance, test_m29_checksum, test_l7_repo_normalisation):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== AI: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        shutil.rmtree(_TMP, ignore_errors=True)
        from mico360.hard_exit import hard_exit
        hard_exit(rc)
