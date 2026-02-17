import asyncio
from ingest_runner import ingest_hours

if __name__ == "__main__":
    import sys
    h = 24
    if len(sys.argv) > 1:
        h = int(sys.argv[1])
    asyncio.run(ingest_hours(h))
