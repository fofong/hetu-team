"""Entry: BOTTEAM_DB=/path/events.db python -m botteam_kernel [port]."""
import os

import uvicorn

from .server import create_app


def main() -> None:
    db = os.environ.get("BOTTEAM_DB", "botteam-events.db")
    port = int(os.environ.get("BOTTEAM_PORT", "8790"))
    app = create_app(db_path=db)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
