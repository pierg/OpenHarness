import logging
import sys
from loguru import logger


class InterceptHandler(logging.Handler):
    """
    Intercepts standard logging messages and routes them to Loguru.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(debug: bool = False) -> None:
    """Configures loguru as the central logger and intercepts standard library logging."""
    logger.remove()
    log_level = "DEBUG" if debug else "INFO"

    if debug:
        # Detailed format for debugging
        log_format = (
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
    else:
        # Very concise format for normal use
        log_format = "<green>{time:HH:mm:ss}</green> | <level>{message}</level>"

    logger.add(
        sys.stderr,
        level=log_level,
        colorize=True,
        format=log_format,
    )

    # Intercept all standard logging messages
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Suppress noisy warnings from google.genai
    logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)
