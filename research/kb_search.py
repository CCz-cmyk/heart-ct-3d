# -*- coding: utf-8 -*-
"""科研知识库检索器（BM25，纯标准库实现，中英混排友好）。

用法：
    python research/kb_search.py "层厚对钙化体积误差的影响" --k 3
    python research/kb_search.py "Cavalieri 体积估计" --k 3 --json

供科研智能体（research/agent.py）作为 kb_search 工具调用，检索结果以带编号的引用块
回注提示词，使模型输出必须挂文献编号，事后可逐条核对。
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "research" / "kb" / "papers.json"

STOP = set("""的 了 和 与 对 在 是 为 及 中 上 下 有 无 个 被 把 从 到 会 可以 以及 等 之 其
a an the of in on for to and or is are be as at by with from that this these those""".split())


def tokenize(text):
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(list(run))
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return [t for t in tokens if t and t not in STOP]


class KB:
    def __init__(self, path=KB_PATH):
        data = json.load(open(path, encoding="utf-8"))
        self.papers = data["papers"]
        self.meta = data.get("meta", {})
        self.docs = []
        for p in self.papers:
            text = " ".join([
                p.get("title", ""),
                " ".join(p.get("authors", [])),
                p.get("venue", ""),
                " ".join(p.get("tags", [])),
                p.get("findings", ""),
                p.get("relevance", ""),
            ])
            toks = tokenize(text)
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self.docs.append({"paper": p, "tf": tf, "len": len(toks)})
        self.avgdl = sum(d["len"] for d in self.docs) / max(1, len(self.docs))
        self.df = {}
        for d in self.docs:
            for t in d["tf"]:
                self.df[t] = self.df.get(t, 0) + 1
        self.N = len(self.docs)

    def idf(self, term):
        df = self.df.get(term, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def search(self, query, k=3, k1=1.2, b=0.75):
        q = tokenize(query)
        if not q:
            return []
        scored = []
        for d in self.docs:
            s = 0.0
            for t in set(q):
                f = d["tf"].get(t, 0)
                if not f:
                    continue
                s += self.idf(t) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d["len"] / self.avgdl))
            if s > 0:
                scored.append((s, d["paper"]))
        scored.sort(key=lambda x: -x[0])
        hits = []
        for s, p in scored[:k]:
            hits.append({
                "id": p["id"],
                "title": p["title"],
                "venue": p["venue"],
                "year": p.get("year"),
                "url": p.get("url"),
                "findings": p.get("findings", ""),
                "relevance": p.get("relevance", ""),
                "caveat": p.get("caveat", ""),
                "score": round(s, 3),
            })
        return hits


def format_for_prompt(hits):
    """把检索结果格式化成可注入提示词的引用块，强制模型按编号引用。"""
    if not hits:
        return "（知识库未检索到相关条目）"
    lines = []
    for h in hits:
        lines.append("[%s] %s. %s (%s).\n  要点：%s\n  适用：%s" % (
            h["id"], h["title"], h["venue"], h["year"], h["findings"], h["relevance"]))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    kb = KB()
    hits = kb.search(args.query, args.k)
    if args.json:
        print(json.dumps({"query": args.query, "hits": hits}, ensure_ascii=False, indent=2))
    else:
        print("知识库：%s（%d 条）" % (kb.meta.get("name", ""), kb.N))
        print("查询：%s\n" % args.query)
        for h in hits:
            print("[%s] (%.2f) %s" % (h["id"], h["score"], h["title"]))
            print("     %s (%s)" % (h["venue"], h["year"]))
            print("     要点：%s" % h["findings"])
            print("     适用：%s" % h["relevance"])
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
