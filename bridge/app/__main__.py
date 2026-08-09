"""Entry point: `python -m bridge.app`."""

import uvicorn

from bridge.app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("bridge.app.main:app", host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
