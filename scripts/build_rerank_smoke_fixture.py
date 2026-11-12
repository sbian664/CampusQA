"""Build a deterministic TIMING/REGRESSION fixture, not a relevance-labelled test set."""
import argparse
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(documents: Path, candidate_count: int = 30):
    from src.text_chunker import SemanticChunker
    chunker = SemanticChunker()
    pool = []
    for path in sorted(documents.glob('*.txt')):
        for i, text in enumerate(chunker.split_text(path.read_text(errors='replace'), str(path))):
            if len(text.strip()) > 100:
                pool.append({'id': f'{path.stem}:{i}', 'content': text})
    if candidate_count < 1 or len(pool) < candidate_count:
        raise ValueError('Not enough document chunks for the requested candidate count')
    # Seed a few plausible candidates plus diverse distractors. File-name
    # matching is NOT a relevance label and must not be reported as accuracy.
    queries = [
        ('enrollment-en', 'What should undergraduate students do to enroll in classes?', 'Enrollment'),
        ('enrollment-zh', '本科生如何进行选课，选课时有哪些限制？', 'Enrollment'),
        ('facilities-en', 'What sports facilities are available on campus?', 'Sports'),
        ('courses-en', 'What are the prerequisites and topics of AIAA1010?', 'AIAA1010'),
        ('graduation-zh', '学生毕业需要满足哪些要求？', 'Graduations'),
        ('dorm-en', 'What facilities are available in student dormitories?', 'Dormitor'),
    ]
    rng = random.Random(20261112)
    cases = []
    for cid, query, needle in queries:
        chosen = [x for x in pool if needle.lower() in x['id'].lower()][:min(6, candidate_count)]
        ids = {x['id'] for x in chosen}
        chosen += rng.sample([x for x in pool if x['id'] not in ids], candidate_count - len(chosen))
        rng.shuffle(chosen)
        cases.append({'id': cid, 'query': query, 'candidates': chosen})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--documents', type=Path, default=ROOT / 'data/documents')
    parser.add_argument('--candidates', type=int, default=30)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new path to preserve comparability')
    cases = build(args.documents, args.candidates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cases))
    print(f'{len(cases)} cases x {args.candidates} candidates; NOT relevance-labelled: {args.output}')


if __name__ == '__main__':
    main()
