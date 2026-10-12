#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Один проход докачки переводов недостающих японских строк (ja->ru).
Пакеты с SEP-разделителем; при рассинхроне — поштучно. Сохраняетtranslations.json атомарно."""
import json, re, time, threading, queue, requests, os, sys

JP_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]')
SRC = "extract.json"
OUT = "translations.json"


def span_text(s):
    return "".join(c["c"] for c in s["chars"])


def main():
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
    cache = json.load(open(OUT))
    missing = [t for t in sorted(uniq) if not cache.get(t)]
    print("todo", len(missing), flush=True)
    if not missing:
        return

    session = requests.Session()
    lock = threading.Lock()
    done = [0]
    SEP = "\u2016"

    def gtx(qtext):
        r = session.get("https://translate.googleapis.com/translate_a/single",
                        params={"client": "gtx", "sl": "ja", "tl": "ru", "dt": "t", "q": qtext},
                        timeout=20)
        r.raise_for_status()
        return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])

    taskq = queue.Queue()
    for i in range(0, len(missing), 20):
        taskq.put(missing[i:i + 20])

    last_save = [0.0]

    def save(force=False):
        now = time.time()
        if not force and now - last_save[0] < 8:
            return
        last_save[0] = now
        tmp = OUT + ".tmp"
        with lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, OUT)

    def worker():
        while True:
            try:
                texts = taskq.get(timeout=1)
            except queue.Empty:
                return
            res = {}
            # пакетная попытка
            try:
                out = gtx(SEP.join(texts))
                parts = out.split(SEP)
                if len(parts) == len(texts):
                    for t, tr in zip(texts, parts):
                        tr = re.sub(r"\s+", " ", tr).strip()
                        if tr and not JP_RE.search(tr):
                            res[t] = tr
            except Exception:
                pass
            left = [t for t in texts if t not in res]
            # поштучно для оставшихся (с коротким таймаутом на неудачи)
            fails = 0
            for t in left:
                if fails >= 3:
                    break  # вероятно бан — выходим, остальные в следующем проходе
                ok = False
                for attempt in range(2):
                    try:
                        tr = re.sub(r"\s+", " ", gtx(t)).strip()
                        if tr and not JP_RE.search(tr):
                            res[t] = tr
                            ok = True
                        break
                    except Exception:
                        time.sleep(0.5 * (attempt + 1))
                if not ok:
                    fails += 1
            with lock:
                cache.update(res)
                done[0] += len(res)
            save()
            print(f"progress {done[0]}/{len(missing)}", flush=True)

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(6)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    save(True)
    print("PASS_DONE translated", done[0], "of", len(missing), flush=True)


if __name__ == "__main__":
    main()
