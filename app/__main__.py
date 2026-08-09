import argparse

import uvicorn

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(prog="MyReader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
