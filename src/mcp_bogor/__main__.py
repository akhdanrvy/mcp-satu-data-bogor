"""Entry `python -m mcp_bogor`: server MCP (stdio bawaan; MCP_TRANSPORT=streamable-http → :8800).

File server data_ref :8799 dijalankan terpisah: `python -m mcp_bogor.fileserver`.
"""

import sys

from mcp_bogor.runtime import main

if __name__ == "__main__":
    sys.exit(main())
