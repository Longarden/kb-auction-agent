"""단일 조합을 별도 프로세스에서 실행해 결정적 JSON 을 stdout 으로 낸다.

크로스프로세스 결정성 테스트가 쓴다. 같은 프로세스 안에서 두 번 호출하는
방식으로는 PYTHONHASHSEED 에 따른 집합 순회 순서 차이를 잡을 수 없다.

    python -m tests.helpers.run_one case_002_tenant user_young
"""
from __future__ import annotations

import sys

from src.schemas.core import PropertyCase, UserProfile
from src.utils.config import load_config
from src.utils.io import read_json


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: run_one <case_name> <user_name>\n")
        return 2
    case_name, user_name = argv

    from src.orchestrator import run as orch_run

    cfg = load_config()
    case = PropertyCase.model_validate(read_json(f"data/samples/{case_name}/case.json"))
    raw = read_json("data/samples/users.json")[user_name]
    user = UserProfile.model_validate({k: v for k, v in raw.items() if k != "label"})

    verdict = orch_run(case, user, cfg)
    sys.stdout.write(verdict.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
