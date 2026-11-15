"""校验归档的幂等性与静态 HTTP 可达性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app
from app.services import output_index as oi

UID = "OwdN5Jwjo_9XA_iAvuht-Q"


def main() -> int:
    client = TestClient(app)
    root = oi.backend_root() / "outputs"
    base = root / "_archived" / UID / "e2e-card-verify"

    print("=== 1. 静态 HTTP 访问 ===")
    bad = 0
    checked = 0
    for group_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        first = sorted(group_dir.glob("*.png"))[0]
        rel = first.relative_to(root).as_posix()
        resp = client.get(f"/outputs/{rel}")
        checked += 1
        if resp.status_code != 200 or not resp.content:
            bad += 1
            print(f"  FAIL {resp.status_code} {rel}")
        else:
            print(f"  OK   {resp.status_code} {len(resp.content):>8d} bytes  {rel}")
    print(f"抽查 {checked} 组，失败 {bad}")

    print()
    print("=== 2. 归档产物条数（幂等基线）===")
    count = len(list(base.rglob("*.png")))
    print("归档 PNG 文件数 =", count)

    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())