import json, re, os, sys, time, threading, queue
import requests

SRC = "extract.json"
OUT = "translations.json"
PROG = "progress.txt"

def span_text(s):
    return "".join(c["c"] for c in s["chars"])

JP_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]')

data = json.load(open(SRC))
uniq = set()
for pg in data:
    for b in pg["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                t = span_text(s).strip()
                if t and JP_RE.search(t):
                    uniq.add(t)
todo_all = sorted(uniq)

cache = {}
if os.path.exists(OUT):
    try:
        cache = json.load(open(OUT))
    except Exception:
        cache = {}
todo = [t for t in todo_all if t not in cache or not cache[t]]
print(f"unique={len(todo_all)} cached={len(todo_all)-len(todo)} todo={len(todo)}", flush=True)

NTHREADS = 12
BATCH_LIMIT = 4500  # chars per request

session = requests.Session()
lock = threading.Lock()
counter = {"done": 0, "fail": 0}
stop_flag = threading.Event()

def google_batch(texts):
    """texts: list of strings -> list of translated strings (joined segments split back)."""
    q = "\n".join(texts)
    params = {"client": "gtx", "sl": "ja", "tl": "ru", "dt": "t", "q": q}
    r = session.get("https://translate.googleapis.com/translate_a/single",
                    params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"http {r.status_code}")
    j = r.json()
    segs = j[0]
    parts = []
    for seg in segs:
        if seg and seg[0]:
            parts.append(seg[0])
        else:
            parts.append("")
    joined = "".join(parts)
    # Google preserves newlines as separate segments sometimes; split robustly
    lines = joined.split("\n")
    # If translation collapsed some newlines into spaces, fallback: join/split heuristics won't be perfect.
    return lines

def worker():
    buf = []
    buflen = 0
    while not stop_flag.is_set():
        try:
            item = taskq.get(timeout=1)
        except queue.Empty:
            if buf:
                flush(buf); buf = []
            continue
        if item is None:
            if buf:
                flush(buf)
            return
        buf.append(item)
        buflen += len(item) + 1
        if buflen >= BATCH_LIMIT:
            flush(buf); buf = []; buflen = 0
    if buf:
        flush(buf)

def flush(buf):
    texts = buf
    # process in chunks respecting limit
    chunk = []
    clen = 0
    for t in texts:
        chunk.append(t)
        clen += len(t) + 1
        if clen >= BATCH_LIMIT:
            do_request(chunk); chunk = []; clen = 0
    if chunk:
        do_request(chunk)

def do_request(texts):
    if len(texts) == 1:
        t = texts[0]
        tries = 0
        while tries < 6 and not stop_flag.is_set():
            tries += 1
            try:
                lines = google_batch([t])
                tr = "\n".join(lines).strip()
                if not tr:
                    tr = t  # keep original if empty translation
                with lock:
                    cache[t] = tr
                    counter["done"] += 1
                save()
                return
            except Exception as e:
                time.sleep(2 * tries)
                if isinstance(e, RuntimeError) and "http 4" in str(e):
                    stop_flag.set()
        with lock:
            counter["fail"] += 1
            print("FAILED single:", t[:30], flush=True)
        return
    # split batch in half recursively
    mid = len(texts) // 2
    do_request(texts[:mid])
    do_request(texts[mid:])

last_save = 0
def save():
    global last_save
    now = time.time()
    if now - last_save < 5:
        return
    last_save = now
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    with open(PROG, "w") as f:
        f.write(f"done={counter['done']} fail={counter['fail']} total={len(todo)}\n")

taskq = queue.Queue()
for t in todo:
    taskq.put(t)
threads = [threading.Thread(target=worker, daemon=True) for _ in range(NTHREADS)]
for th in threads:
    th.start()
# send sentinels
for _ in threads:
    taskq.put(None)
for th in threads:
    th.join()
save()
with open(PROG, "w") as f:
    f.write(f"done={counter['done']} fail={counter['fail']} total={len(todo)} finished\n")
print("DONE", counter, flush=True)
