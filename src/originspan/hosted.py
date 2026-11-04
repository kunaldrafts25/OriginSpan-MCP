"""OriginSpan entrypoint for a single-service hosted deployment."""
from diamond_mcp.hosted import main

if __name__ == "__main__":
    raise SystemExit(main())
