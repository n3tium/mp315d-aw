#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Докачивание переводов в translations.json (ja->ru) через Google gtx.
Пакетные запросы с восстановлением построчно; при рассинхроне — рекурсивный бисект."""
import json, re, os, sys, time, threading, queue
import requests

SRC = "extract.json"
OUT = "translations.json"
PROG = "progress.txt"

JP_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]')


def span_text(s):
    return "".join(c["c"] for c in s["chars"])


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
todo = [t for t in todo_all if not cache.get(t)]
print(f"unique={len(todo_all)} cached={len(todo_all)-len(todo)} todo={len(todo)}", flush=True)

NTHREADS = 8
BATCH_LIMIT = 1200  # символов на пакетный запрос

session = requests.Session()
lock = threading.Lock()
counter = {"done": 0, "fail": 0}
stop_flag = threading.Event()


def gtx(qtext):
    r = session.get("https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": "ja", "tl": "ru", "dt": "t", "q": qtext},
                    timeout=30)
    if r.status_code == 403 or r.status_code == 429:
        raise RuntimeError(f"http {r.status_code}")
    r.raise_for_status()
    j = r.json()
    return "".join(seg[0] for seg in j[0] if seg and seg[0])


def translate_batch(texts):
    """Возвращает dict text->translation для пакета."""
    joined = "\n".join(texts)
    out = gtx(joined)
    lines = out.split("\n")
    res = {}
    if len(lines) == len(texts):
        for t, tr in zip(texts, lines):
            tr = tr.strip()
            if tr and not JP_RE.search(tr):
                res[t] = tr
    else:
        # рассинхрон строк — делим пакет пополам
        if len(texts) == 1:
            t = texts[0]
            try:
                tr = gtx(t).strip()
                if tr and not JP_RE.search(tr):
                    res[t] = tr
            except Exception:
                pass
        else:
            mid = len(texts) // 2
            res.update(translate_batch(texts[:mid]))
            res.update(translate_batch(texts[mid:]))
    return res


last_save = 0.0


def save():
    global last_save
    now = time.time()
    if now - last_save < 5:
        return
    last_save = now
    tmp = OUT + ".tmp"
    with lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    with open(PROG, "w") as f:
        f.write(f"done={counter['done']} fail={counter['fail']} total={len(todo)}\n")


def worker(taskq):
    while not stop_flag.is_set():
        texts = []
        buflen = 0
        deadline = time.time() + 2.0
        while len(texts) < 60 and buflen < BATCH_LIMIT:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                item = taskq.get(timeout=min(remain, 0.5))
            except queue.Empty:
                continue
            if item is None:
                taskq.put(None)
                break
            texts.append(item)
            buflen += len(item) + 1
            if texts[-1].endswith(("。", "：")):
                deadline = min(deadline, time.time() + 0.7)
        if not texts:
            if taskq.empty():
                return
            continue
        tries = 0
        while tries < 5 and not stop_flag.is_set():
            tries += 1
            try:
                res = translate_batch(texts)
                with lock:
                    cache.update(res)
                    counter["done"] += len(res)
                    counter["fail"] += len(texts) - len(res)
                save()
                break
            except Exception as e:
                if "http 4" in str(e):
                    time.sleep(5 * tries)
                    if tries >= 3:
                        stop_flag.set()
                else:
                    time.sleep(2 * tries)


taskq = queue.Queue()
for t in todo:
    taskq.put(t)
threads = [threading.Thread(target=worker, args=(taskq,), daemon=True) for _ in range(NTHREADS)]
for th in threads:
    th.start()
for th in threads:
    th.join()
save()
with open(PROG, "w") as f:
    f.write(f"done={counter['done']} fail={counter['fail']} total={len(todo)} finished\n")
print("DONE", counter, flush=True)
