#!/usr/bin/env python3
"""Seeded generator of SYNTHETIC patient replies for the triage model (D13).

    python tools/triage/generate_dataset.py [--seed 20261018] [--out tools/triage/dataset] [--per-intent 340]

Writes, byte-identically for a given seed:

    train.jsonl, val.jsonl, test.jsonl   one JSON object per line: id, text, label, language, variant, template
    manifest.json                        seed, counts, sha256 of each file, the template split
    ../../docs/results/triage/h2_review_sample.csv   100 lines for the clinical/linguistic reviewer (H2)

Leakage cannot happen by construction: the split is BY TEMPLATE. Every template belongs to exactly one of
train / val / test (about 70 / 12 / 18 per intent and variant), a text is generated only from its own template's
split, and a text that happens to be produced by templates of two splits is dropped from the later split (train
loses it). `check_no_leakage` re-verifies both properties and the tests run it.

The data are NOT real patient language. They are templated by hand, with slot variation, typos, Arabic
orthographic variants, Arabic-Indic digits, emojis, Arabizi and mixed-script messages. A model that scores well
here has learned these templates' families, not the language of patients; see the model card for what that means.
Only the standard library is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from templates import COMPOUND, EMOJI_ONLY, INTENTS, POOLS, SUFFIX_POOL, TEMPLATES, VARIANTS  # noqa: E402

DEFAULT_SEED = 20261018
DEFAULT_OUT = HERE / "dataset"
REVIEW_PATH = HERE.parents[1] / "docs/results/triage/h2_review_sample.csv"
PER_INTENT = 340  # examples per intent before de-duplication
LANGUAGE_OF = {"en": "en", "ar_msa": "ar", "ar_gulf": "ar", "arabizi": "arabizi", "mixed": "mixed"}
EMOJIS = ["🙏", "👍", "😊", "😔", "❤️", "🌹", "😅", "🤲", "🙂", "😞"]
SLOT_RE = re.compile(r"\{(\w+)\}")
ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
ARABIC_RE = re.compile(r"[؀-ۿ]")


def all_templates() -> dict[str, dict[str, list[str]]]:
    """The hand-written templates plus the compound ones, as intent -> variant -> [template]."""
    out = {i: {v: list(TEMPLATES[i].get(v, [])) for v in VARIANTS} for i in INTENTS}
    for intent, by_variant in COMPOUND.items():
        for variant, items in by_variant.items():
            out[intent][variant].extend(items)
    return out


def split_templates(seed: int) -> dict[tuple[str, str, int], str]:
    """(intent, variant, index) -> 'train' | 'val' | 'test'. About 18% test and 12% val of every
    (intent, variant) group, at least one test template when the group has four or more."""
    rng = random.Random(f"{seed}|split")
    assignment: dict[tuple[str, str, int], str] = {}
    for intent, by_variant in all_templates().items():
        for variant in VARIANTS:
            items = by_variant[variant]
            order = list(range(len(items)))
            rng.shuffle(order)
            n = len(items)
            n_test = max(1, round(n * 0.18)) if n >= 4 else 0
            n_val = max(1, round(n * 0.12)) if n >= 6 else 0
            for rank, idx in enumerate(order):
                assignment[(intent, variant, idx)] = "test" if rank < n_test else "val" if rank < n_test + n_val else "train"
    return assignment


# ----------------------------------------------------------------------------------------------- filling
def _pool(variant: str, slot: str) -> list[str]:
    if variant == "mixed" and "_" in slot:
        base, suffix = slot.rsplit("_", 1)
        if suffix in SUFFIX_POOL:
            return POOLS[SUFFIX_POOL[suffix]][base]
    pool_variant = "ar_gulf" if variant == "mixed" else variant
    return POOLS[pool_variant][slot]


def fill(template: str, variant: str, rng: random.Random) -> str:
    text = SLOT_RE.sub(lambda m: rng.choice(_pool(variant, m.group(1))), template)
    return re.sub(r"\s+", " ", text).strip(" ،,").strip()


def _typo(word: str, rng: random.Random) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(("drop", "dup", "swap"))
    if kind == "drop":
        return word[:i] + word[i + 1:]
    if kind == "dup":
        return word[:i] + word[i] + word[i:]
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


ARABIC_VARIANTS = (("أ", "ا"), ("إ", "ا"), ("ة", "ه"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي"))


def perturb(text: str, variant: str, rng: random.Random) -> str:
    """Typos, orthographic variants, digits, punctuation, case and emojis. Seeded by the caller."""
    words = text.split(" ")
    if rng.random() < 0.28:  # a typo in one Latin word
        latin = [i for i, w in enumerate(words) if w.isascii() and w.isalpha() and len(w) >= 4]
        if latin:
            i = rng.choice(latin)
            words[i] = _typo(words[i], rng)
    text = " ".join(words)
    if ARABIC_RE.search(text) and rng.random() < 0.35:  # orthographic variation, as phone keyboards produce it
        a, b = rng.choice(ARABIC_VARIANTS)
        text = text.replace(a, b)
    if ARABIC_RE.search(text) and rng.random() < 0.15:  # a dropped Arabic letter
        idx = [i for i, c in enumerate(text) if ARABIC_RE.match(c)]
        if len(idx) > 8:
            j = rng.choice(idx[1:-1])
            text = text[:j] + text[j + 1:]
    if variant in ("ar_msa", "ar_gulf") and rng.random() < 0.3:  # Arabic-Indic digits, only where the text is Arabic
        text = text.translate(ARABIC_INDIC)
    if variant in ("en", "arabizi", "mixed"):
        roll = rng.random()
        if roll < 0.18:
            text = text.lower()
        elif roll < 0.22:
            text = text.upper()
    roll = rng.random()
    if roll < 0.2:
        text = text.rstrip(".?!؟،,")
    elif roll < 0.28:
        text = text.rstrip(".?!؟،,") + rng.choice(["!!", "..", "!", "???"] if variant != "ar_msa" and variant != "ar_gulf" else ["!!", "..", "!", "؟؟"])
    if rng.random() < 0.3:
        emoji = rng.choice(EMOJIS)
        text = f"{emoji} {text}" if rng.random() < 0.25 else f"{text} {emoji}"
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------------------------------- generation
def generate(seed: int = DEFAULT_SEED, per_intent: int = PER_INTENT) -> dict[str, list[dict]]:
    assignment = split_templates(seed)
    templates = all_templates()
    rows: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    seen: dict[str, str] = {}  # text -> split that claimed it first (test, val, train: in that order)
    for split in ("test", "val", "train"):
        for intent in INTENTS:
            members = [(v, i) for v in VARIANTS for i in range(len(templates[intent][v]))
                       if assignment[(intent, v, i)] == split]
            if not members:
                continue
            share = {"train": 0.70, "val": 0.12, "test": 0.18}[split]
            want = round(per_intent * share)
            produced = 0
            attempts = 0
            made: set[str] = set()
            while produced < want and attempts < want * 12:
                variant, idx = members[attempts % len(members)]
                rng = random.Random(f"{seed}|{intent}|{variant}|{idx}|{attempts}")
                attempts += 1
                text = perturb(fill(templates[intent][variant][idx], variant, rng), variant, rng)
                if not text or text in made or (text in seen and seen[text] != split):
                    continue
                made.add(text)
                seen[text] = split
                produced += 1
                rows[split].append({
                    "id": f"{split[:2]}-{len(rows[split]) + 1:05d}", "text": text, "label": intent,
                    "language": LANGUAGE_OF[variant], "variant": variant, "template": f"{intent}.{variant}.{idx}",
                })
    # emoji-only messages, spread over the splits by position (they are not templates)
    for label, items in EMOJI_ONLY.items():
        for k, emoji in enumerate(items):
            split = "train" if k % 4 < 2 else "val" if k % 4 == 2 else "test"
            if emoji in seen:
                continue
            seen[emoji] = split
            rows[split].append({"id": "", "text": emoji, "label": label, "language": "mixed", "variant": "emoji",
                                "template": f"{label}.emoji.{k}"})
    for split in rows:  # a stable shuffle, so files are not grouped by intent
        random.Random(f"{seed}|shuffle|{split}").shuffle(rows[split])
        for n, row in enumerate(rows[split], 1):
            row["id"] = f"{split[:2]}-{n:05d}"
    return rows


def check_no_leakage(rows: dict[str, list[dict]]) -> None:
    """Raise AssertionError if a template or a text is shared between splits."""
    templates = {s: {r["template"] for r in rs} for s, rs in rows.items()}
    texts = {s: {r["text"] for r in rs} for s, rs in rows.items()}
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        assert not templates[a] & templates[b], f"template shared by {a} and {b}: {sorted(templates[a] & templates[b])[:3]}"
        assert not texts[a] & texts[b], f"text shared by {a} and {b}: {sorted(texts[a] & texts[b])[:3]}"


# ------------------------------------------------------------------------------------------------ output
def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def review_sample(rows: dict[str, list[dict]], seed: int, n: int = 100) -> list[dict]:
    """A stratified draw of `n` lines over all splits: about n/9 per intent, spread across the variants."""
    pool = [r for s in ("train", "val", "test") for r in rows[s]]
    rng = random.Random(f"{seed}|review")
    chosen: list[dict] = []
    per = n // len(INTENTS)
    for intent in INTENTS:
        group = [r for r in pool if r["label"] == intent]
        rng.shuffle(group)
        picked, used = [], set()
        for variant in (*VARIANTS, "emoji"):  # one per variant first, then fill
            for r in group:
                if r["variant"] == variant and r["id"] not in used:
                    picked.append(r)
                    used.add(r["id"])
                    break
        for r in group:
            if len(picked) >= per:
                break
            if r["id"] not in used:
                picked.append(r)
                used.add(r["id"])
        chosen.extend(picked[:per])
    rest = [r for r in pool if r not in chosen]
    rng.shuffle(rest)
    chosen.extend(rest[: n - len(chosen)])
    rng.shuffle(chosen)
    return chosen[:n]


def write_all(out: Path, seed: int, per_intent: int, review_path: Path | None = REVIEW_PATH) -> dict:
    rows = generate(seed, per_intent)
    check_no_leakage(rows)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"seed": seed, "per_intent": per_intent, "synthetic": True, "files": {}, "counts": {}}
    for split, items in rows.items():
        text = _jsonl(items)
        (out / f"{split}.jsonl").write_text(text, encoding="utf-8")
        manifest["files"][f"{split}.jsonl"] = hashlib.sha256(text.encode()).hexdigest()
        manifest["counts"][split] = {
            "total": len(items),
            "by_label": {i: sum(1 for r in items if r["label"] == i) for i in INTENTS},
            "by_language": {lang: sum(1 for r in items if r["language"] == lang) for lang in sorted(set(LANGUAGE_OF.values()))},
            "templates": len({r["template"] for r in items}),
        }
    manifest["total"] = sum(len(v) for v in rows.values())
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    if review_path is not None:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with review_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["id", "language", "variant", "text", "generator_label", "reviewer_label", "reviewer_comment"])
            for r in review_sample(rows, seed):
                w.writerow([r["id"], r["language"], r["variant"], r["text"], r["label"], "", ""])
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--per-intent", type=int, default=PER_INTENT)
    ap.add_argument("--check", action="store_true", help="fail if the committed files differ from a fresh generation")
    args = ap.parse_args(argv)
    if args.check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fresh = write_all(Path(tmp), args.seed, args.per_intent, review_path=None)
        committed = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
        if fresh != committed:
            print("dataset differs from a fresh generation", file=sys.stderr)
            return 1
        print("dataset matches a fresh generation")
        return 0
    manifest = write_all(args.out, args.seed, args.per_intent)
    print(json.dumps({k: manifest[k] for k in ("seed", "total")} | {"counts": {s: c["total"] for s, c in manifest["counts"].items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
