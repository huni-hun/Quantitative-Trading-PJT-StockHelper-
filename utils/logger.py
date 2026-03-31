import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "trading_bot.log")

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ensure_log_dir() -> None:
    """logs 디렉터리가 없으면 생성한다."""
    os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """콘솔 핸들러와 로테이팅 파일 핸들러가 설정된 이름 기반 로거를 반환한다.

    Args:
        name:  로거 이름. 일반적으로 호출 모듈의 ``__name__`` 을 사용한다.
        level: 최소 로그 레벨 (기본값: DEBUG).

    Returns:
        logging.Logger: 설정이 완료된 로거 인스턴스.
    """
    logger = logging.getLogger(name)

    # 동일 로거를 여러 번 가져올 때 핸들러 중복 등록 방지
    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 로테이팅 파일 핸들러 (파일당 최대 5MB, 최근 3개 파일 유지)
    _ensure_log_dir()
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
