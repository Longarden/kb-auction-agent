"""단일 IO 게이트. src/ 와 app/ 에서 raw open() 을 쓰지 않는다.

이유: Windows 기본 인코딩이 cp949 라서 encoding 을 명시하지 않으면
한글 파일 읽기가 UnicodeDecodeError 로 즉시 실패한다.
정책 테스트(tests/test_encoding_policy.py)가 이 규칙을 강제한다.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve(path: str | Path) -> Path:
    """상대경로는 레포 루트 기준으로 해석한다(실행 위치에 무관하게 만들기 위함)."""
    p = Path(path)
    return p if p.is_absolute() else (REPO_ROOT / p)


def read_text(path: str | Path) -> str:
    """UTF-8 로 읽고 개행과 유니코드 표기를 정규화한다.

    NFKC 정규화가 필요한 이유: 매각물건명세서 텍스트에는 전각 숫자·괄호
    (예: ２０２３．０５．１１)가 흔해서 정규화 없이는 정규식이 조용히 실패한다.
    """
    raw = resolve(path).read_text(encoding="utf-8")
    return unicodedata.normalize("NFKC", raw.replace("\r\n", "\n"))


def read_yaml(path: str | Path) -> Any:
    return yaml.safe_load(read_text(path))


def read_json(path: str | Path) -> Any:
    return json.loads(read_text(path))


def write_text(content: str, path: str | Path) -> Path:
    target = resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def write_json(obj: Any, path: str | Path) -> Path:
    return write_text(json.dumps(obj, ensure_ascii=False, indent=2), path)
