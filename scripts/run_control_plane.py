"""Run the paper/shadow control plane locally; real trading is unavailable."""

from qtrade.api import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
