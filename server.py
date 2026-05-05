"""
server.py — convenience startup script
Run from c:\\apps\\swucards\\:
    python server.py
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["api", "frontend"],
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "%(levelprefix)s %(message)s",
                    "use_colors": False,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                    "use_colors": False,
                },
            },
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"},
                "access":  {"class": "logging.StreamHandler", "formatter": "access",  "stream": "ext://sys.stdout"},
            },
            "loggers": {
                "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error":  {"level": "INFO"},
                "uvicorn.access": {"handlers": ["access"],  "level": "INFO", "propagate": False},
            },
        },
    )
