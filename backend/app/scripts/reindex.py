import re
from pathlib import Path

from dotenv import load_dotenv

from app.db.database import PROJECT_ROOT, init_db, reset_db
from app.services.embedding import get_embeddings_batch
from app.services.rag import serialize_f32

load_dotenv(PROJECT_ROOT / "backend" / ".env")

KNOWLEDGE_DIRS = [
    PROJECT_ROOT / "knowledge",
    PROJECT_ROOT / "general-lol-wiki",
]

MIN_CHUNK_LEN = 100
SECTION_SOFT_LIMIT = 1500
SUB_CHUNK_SIZE = 800

TAG_PATTERN = re.compile(r"\[태그\]\s*(.+)")
HEADER_SPLIT_PATTERN = re.compile(r"(?m)^## ")


def split_markdown(content: str) -> list[str]:
    sections = HEADER_SPLIT_PATTERN.split(content)
    chunks: list[str] = []

    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        if i > 0:
            section = "## " + section

        if len(section) > SECTION_SOFT_LIMIT:
            for j in range(0, len(section), SUB_CHUNK_SIZE):
                sub = section[j : j + SUB_CHUNK_SIZE].strip()
                if len(sub) >= MIN_CHUNK_LEN:
                    chunks.append(sub)
        else:
            if len(section) >= MIN_CHUNK_LEN:
                chunks.append(section)

    return chunks


def parse_tags(chunk: str) -> str:
    match = TAG_PATTERN.search(chunk)
    if match:
        return match.group(1).strip()
    return ""


def iter_markdown_files() -> list[Path]:
    files: list[Path] = []
    for root in KNOWLEDGE_DIRS:
        if not root.exists():
            continue
        files.extend(root.glob("**/*.md"))
    files = sorted(set(files))
    return [f for f in files if f.name.lower() != "readme.md"]


def main() -> None:
    md_files = iter_markdown_files()
    if not md_files:
        roots = ", ".join(str(d) for d in KNOWLEDGE_DIRS)
        print(f"인덱싱할 .md 파일이 없습니다: {roots}")
        return

    conn = init_db()
    reset_db(conn)

    total_chunks = 0

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        chunks = split_markdown(content)
        if not chunks:
            print(f"Skipping {md_file.name}... no valid chunks (min {MIN_CHUNK_LEN} chars)")
            continue

        embeddings = get_embeddings_batch(chunks)

        rel_path = md_file.relative_to(PROJECT_ROOT).as_posix()
        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            tags = parse_tags(chunk)
            cur = conn.execute(
                """
                INSERT INTO knowledge_chunks
                    (source_file, chunk_index, content, tags)
                VALUES (?, ?, ?, ?)
                """,
                (rel_path, idx, chunk, tags),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                (chunk_id, serialize_f32(emb)),
            )

        conn.commit()
        total_chunks += len(chunks)
        print(f"Indexing {md_file.name}... {len(chunks)} chunks created")

    conn.close()
    print(f"\nDone. {len(md_files)} files / {total_chunks} chunks indexed.")


if __name__ == "__main__":
    main()
