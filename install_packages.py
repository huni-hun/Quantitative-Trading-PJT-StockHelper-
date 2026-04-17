"""
이 스크립트를 venv python으로 직접 실행하세요:
  venv\Scripts\python.exe install_packages.py
"""
import subprocess
import sys

packages = [
    "pandas>=2.2,<4.0",
    "numpy>=1.24,<3.0",
    "requests",
    "urllib3",
    "charset-normalizer",
    "idna",
    "certifi",
    "click",
    "colorama",
    "jinja2>=3.1",
    "markupsafe",
    "werkzeug",
    "blinker",
    "itsdangerous",
    "python-dotenv>=1.0",
    "pykrx",
    "openai>=2.0",
    "groq>=1.0",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "feedparser>=6.0",
    "flask>=3.0",
    "flask-cors>=4.0",
]

print(f"Python: {sys.executable}")
print(f"설치 대상: {len(packages)}개 패키지\n")

result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "--ignore-installed"] + packages,
    capture_output=False,  # 직접 콘솔에 출력
)

print(f"\n종료 코드: {result.returncode}")

# 확인
print("\n=== 설치 확인 ===")
for mod in ["numpy", "pandas", "requests", "flask"]:
    try:
        m = __import__(mod)
        print(f"[OK] {mod} {m.__version__}")
    except ImportError as e:
        print(f"[FAIL] {mod}: {e}")
