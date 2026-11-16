from app import app
from monitor.config import load_config

if __name__ == "__main__":
    from waitress import serve

    cfg = load_config()
    serve(app, host=cfg["bind_host"], port=cfg["bind_port"])
