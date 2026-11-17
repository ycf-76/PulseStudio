"""grid 能力的纯类型定义（不依赖 numpy/Pillow）。

独立成模块的原因：三期能力服务化后，主进程在 remote 模式下只重建
检测结果对象（从服务返回的 dict），不应被迫加载 numpy。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class GridDetection:
    """检测结果。is_grid=False 时其它字段无意义。"""

    is_grid: bool = False
    rows: int = 1
    cols: int = 1
    row_seams: list[float] = field(default_factory=list)
    col_seams: list[float] = field(default_factory=list)
    reason: str = ""

    def __bool__(self) -> bool:
        return self.is_grid

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GridDetection":
        return cls(
            is_grid=bool(data.get("is_grid", False)),
            rows=int(data.get("rows", 1)),
            cols=int(data.get("cols", 1)),
            row_seams=list(data.get("row_seams", []) or []),
            col_seams=list(data.get("col_seams", []) or []),
            reason=str(data.get("reason", "")),
        )
