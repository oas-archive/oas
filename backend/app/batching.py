"""把長清單放進 SQL 的 `IN (...)`：SQLite 一句查詢最多 32,766 個參數，作品約 3.3 萬件時會直接出錯。

- chunks：可以分開查再合併結果的（查封面指紋、依編號載入作品），每批 BATCH 個參數。
- id_in：必須是同一句查詢的條件（典藏館搜尋的結果還要再和其他條件一起篩選、排序、分頁），
  把整數編號直接寫進 SQL，不占參數；只收整數，不會被當成 SQL 指令。
"""
from collections.abc import Iterable, Iterator

from sqlalchemy import bindparam

BATCH = 500  # 遠低於 SQLite 的上限；與 SQLAlchemy selectinload 每批的件數相同


def chunks(values: Iterable, size: int = BATCH) -> Iterator[list]:
    """依原本的順序每 size 個切成一批。"""
    items = list(values)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def id_in(column, ids: Iterable[int]):
    """column IN (整數編號…)：編號直接寫進 SQL，件數再多也不會超過參數上限。空清單時沒有任何一筆符合。"""
    values = [int(value) for value in ids]
    return column.in_(bindparam(None, values, expanding=True, literal_execute=True))
